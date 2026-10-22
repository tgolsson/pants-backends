# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Builds an OCI layer tarball from a set of `.deb` packages without running dpkg.

The package contents (`data.tar.*`) are copied into the layer, and each package gets a
`/var/lib/dpkg/status.d/<package>` control file (plus `<package>.md5sums`) in the format used by
distroless images, which is what scanners such as Trivy, Syft and Grype read. Maintainer scripts are
never executed.

Package contents are streamed from the deb into the layer, so memory use doesn't grow with package
size. zstd-compressed members, as Ubuntu uses, are decompressed by piping them through a `zstd`
binary, since the standard library can't read them before Python 3.14. A path shipped by more than
one package is accepted when every copy is identical, as dpkg does for multi-arch packages.

This module only uses the standard library: the `oci_deb_layer` rules copy it into the sandbox and
call `main`, and tests import it directly.
"""

from __future__ import annotations

import argparse
import bz2
import contextlib
import gzip
import hashlib
import io
import lzma
import os
import posixpath
import re
import subprocess
import sys
import tarfile
import threading
import zlib
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import BinaryIO, Callable, Iterator

STATUS_DIR = "var/lib/dpkg/status.d"

# Top-level directories that are symlinks into `/usr` on a merged-/usr system. dpkg follows those
# symlinks when unpacking; a layer can't, so paths are rewritten instead.
MERGED_USR_DIRS = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")

# Debian policy 5.6.1: package names are lowercase alphanumerics plus `+`, `-` and `.`.
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]+")

# PAX header prefixes carrying extended attributes and ACLs, which a GNU-format layer can't hold.
XATTR_HEADERS = ("SCHILY.xattr.", "LIBARCHIVE.xattr.", "SCHILY.acl.", "RHT.security.")


class DebError(Exception):
    pass


@dataclass(frozen=True)
class DebPackage:
    """A deb's metadata, plus where its `data.tar.*` member sits in the still-open deb file."""

    name: str
    control: bytes
    md5sums: bytes | None
    fileobj: BinaryIO = field(repr=False)
    data_member: str
    data_offset: int
    data_size: int
    source: str = ""
    zstd: str | None = None


def read_ar(f: BinaryIO) -> dict[str, tuple[int, int]]:
    """Index the members of an `ar` archive, as used by the `.deb` format, by (offset, size)."""
    f.seek(0)
    if f.read(8) != b"!<arch>\n":
        raise DebError("not an ar archive")

    end = f.seek(0, os.SEEK_END)
    members = {}
    offset = 8
    while offset < end:
        f.seek(offset)
        header = f.read(60)
        if len(header) < 60 or header[58:60] != b"`\n":
            raise DebError(f"malformed ar header at offset {offset}")

        name = header[0:16].decode("ascii").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            raise DebError(f"malformed ar member size at offset {offset}") from None
        start = offset + 60
        if start + size > end:
            raise DebError(f"ar member {name} is truncated")
        members[name] = (start, size)
        offset = start + size + (size % 2)

    return members


class _Slice(io.RawIOBase):
    """A read-only view of `size` bytes at `offset` in a seekable file."""

    def __init__(self, f: BinaryIO, offset: int, size: int) -> None:
        self._f = f
        self._pos = offset
        self._end = offset + size

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        n = min(len(buffer), self._end - self._pos)
        if n <= 0:
            return 0
        self._f.seek(self._pos)
        data = self._f.read(n)
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)


class _HashingReader:
    """Passes reads through, hashing the bytes on the way."""

    def __init__(self, f: BinaryIO) -> None:
        self._f = f
        self.sha256 = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self._f.read(size)
        self.sha256.update(data)
        return data


class _CheckedReader:
    """Turns decompression errors into a `DebError` naming what was being read."""

    def __init__(self, f: BinaryIO, what: str) -> None:
        self._f = f
        self._what = what

    def read(self, size: int = -1) -> bytes:
        try:
            return self._f.read(size)
        except (OSError, EOFError, lzma.LZMAError, zlib.error) as e:
            raise DebError(f"{self._what} is corrupt: {e}") from e


class _ZstdReader:
    """Decompresses a stream by piping it through `zstd -d`, fed from a thread so neither pipe blocks."""

    def __init__(self, zstd: str, f: BinaryIO, what: str) -> None:
        self._what = what
        self._stderr = b""
        self._feed_error: OSError | None = None
        try:
            self._process = subprocess.Popen(
                [zstd, "-d", "-c", "-q"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise DebError(f"{what}: could not run {zstd}: {e}") from e
        self._feeder = threading.Thread(target=self._feed, args=(f,), daemon=True)
        self._feeder.start()

    def _feed(self, f: BinaryIO) -> None:
        assert self._process.stdin is not None
        try:
            while chunk := f.read(1 << 16):
                self._process.stdin.write(chunk)
        except (BrokenPipeError, ValueError):
            # zstd exited early, e.g. on corrupt input, or the reader was closed; `_finish` reports it.
            pass
        except OSError as e:
            # Reading the deb failed; `_finish` reports it rather than zstd's complaint about short input.
            self._feed_error = e
        finally:
            with contextlib.suppress(OSError):
                self._process.stdin.close()

    def read(self, size: int = -1) -> bytes:
        assert self._process.stdout is not None
        if self._process.returncode is not None:
            return b""
        data = self._process.stdout.read(size)
        if not data and size != 0:
            # Report a failure as soon as the output ends, before a reader blames the short stream.
            self._finish(check=True)
        return data

    def __enter__(self) -> _ZstdReader:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._finish(check=exc_type is None)

    def _finish(self, check: bool) -> None:
        assert self._process.stdout is not None and self._process.stderr is not None
        if self._process.returncode is None:
            if not check:
                self._process.kill()
            # Drain what is left, so zstd reaches and verifies the end of the frame.
            while self._process.stdout.read(1 << 16):
                pass
            self._stderr = self._process.stderr.read()
            self._process.wait()
            self._feeder.join()
            self._process.stdout.close()
            self._process.stderr.close()
        if check and self._feed_error is not None:
            raise DebError(f"{self._what}: could not read the package: {self._feed_error}")
        if check and self._process.returncode != 0:
            # zstd names its input in errors, which is only ever stdin here.
            message = self._stderr.decode(errors="replace").strip().replace("/*stdin*\\: ", "")
            raise DebError(
                f"{self._what} is corrupt: {message or f'zstd exited with {self._process.returncode}'}"
            )


def _decompressor(name: str, zstd: str | None = None, what: str = "") -> Callable[[BinaryIO], BinaryIO]:
    """A function wrapping a member's stream in its decompressor. `what` names it in errors."""
    if name.endswith(".tar"):
        return lambda f: f
    if name.endswith(".tar.gz"):
        return lambda f: gzip.GzipFile(fileobj=f, mode="rb")
    if name.endswith(".tar.xz"):
        return lzma.LZMAFile
    if name.endswith(".tar.bz2"):
        return bz2.BZ2File
    if name.endswith(".tar.zst"):
        if zstd is None:
            raise DebError(f"{name}: reading zstd-compressed members needs a zstd binary")
        return lambda f: _ZstdReader(zstd, f, what or name)
    raise DebError(f"{name}: unknown compression")


def _find_member(members: dict[str, tuple[int, int]], prefix: str) -> tuple[str, tuple[int, int]]:
    for name, location in members.items():
        if name.startswith(prefix):
            return name, location
    raise DebError(f"missing {prefix}* member")


def _read_tar_file(tar: tarfile.TarFile, name: str) -> bytes | None:
    for candidate in (f"./{name}", name):
        try:
            member = tar.getmember(candidate)
        except KeyError:
            continue
        extracted = tar.extractfile(member)
        return extracted.read() if extracted else None
    return None


def parse_control(control: bytes) -> list[tuple[str, str]]:
    """Parse the single paragraph of a `control` file into ordered (field, value) pairs.

    Continuation lines are kept verbatim as part of the value.
    """
    fields: list[tuple[str, str]] = []
    # Only `\n` ends a line; `str.splitlines` would also split on characters such as U+2028.
    for line in control.decode("utf-8").split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            if fields:
                break
            continue
        if line[0] in " \t":
            if not fields:
                raise DebError("control file starts with a continuation line")
            key, value = fields[-1]
            fields[-1] = (key, f"{value}\n{line}")
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise DebError(f"malformed control line: {line!r}")
        fields.append((key.strip(), value.strip()))

    return fields


def load_deb(f: BinaryIO, source: str = "", zstd: str | None = None) -> DebPackage:
    """Read a deb's control metadata. `f` must stay open until the package is written to a layer.

    `zstd` is the path to a `zstd` binary, needed for zstd-compressed members.
    """
    try:
        members = read_ar(f)
    except DebError as e:
        raise DebError(f"{source}: {e}") from e

    def read(location: tuple[int, int]) -> bytes:
        offset, size = location
        f.seek(offset)
        return f.read(size)

    if "debian-binary" not in members or read(members["debian-binary"]).strip() != b"2.0":
        raise DebError(f"{source}: unsupported deb format")

    control_name, control_location = _find_member(members, "control.tar")
    data_name, (data_offset, data_size) = _find_member(members, "data.tar")
    # Fail on unsupported compression now rather than halfway through writing the layer.
    try:
        _decompressor(data_name, zstd)
        with _decompressor(control_name, zstd)(io.BytesIO(read(control_location))) as decompressed:
            control_tar = io.BytesIO(_CheckedReader(decompressed, control_name).read())
    except DebError as e:
        raise DebError(f"{source}: {e}") from e
    try:
        with tarfile.open(fileobj=control_tar) as tar:
            control = _read_tar_file(tar, "control")
            md5sums = _read_tar_file(tar, "md5sums")
    except tarfile.TarError as e:
        raise DebError(f"{source}: {control_name} is corrupt: {e}") from e

    if control is None:
        raise DebError(f"{source}: control.tar has no control file")

    fields = dict(parse_control(control))
    if "Package" not in fields:
        raise DebError(f"{source}: control file has no Package field")
    if not PACKAGE_NAME.fullmatch(fields["Package"]):
        raise DebError(f"{source}: invalid package name {fields['Package']!r}")

    return DebPackage(
        name=fields["Package"],
        control=control,
        md5sums=md5sums,
        fileobj=f,
        data_member=data_name,
        data_offset=data_offset,
        data_size=data_size,
        source=source,
        zstd=zstd,
    )


@contextlib.contextmanager
def open_data(package: DebPackage) -> Iterator[tarfile.TarFile]:
    """Stream a package's `data.tar.*`, turning corrupt or truncated archives into a `DebError`."""
    what = f"{package.source or package.name}: {package.data_member}"
    raw = io.BufferedReader(_Slice(package.fileobj, package.data_offset, package.data_size))
    with _decompressor(package.data_member, package.zstd, what)(raw) as decompressed:
        stream = _CheckedReader(decompressed, what)
        try:
            with tarfile.open(fileobj=stream, mode="r|") as data:
                yield data
            # Stream mode stops at the end-of-archive blocks; read on so the decompressor reaches and
            # verifies the stream's checksum and trailer.
            while stream.read(1 << 16):
                pass
        except tarfile.TarError as e:
            raise DebError(f"{what} is corrupt: {e}") from e


def status_entry(control: bytes) -> bytes:
    """Render a package's control paragraph as an installed dpkg status entry."""
    fields = [(k, v) for k, v in parse_control(control) if k != "Status"]
    rendered = []
    for key, value in fields:
        rendered.append(f"{key}: {value}")
        if key == "Package":
            rendered.append("Status: install ok installed")
    return ("\n".join(rendered) + "\n").encode("utf-8")


def clean_path(path: str) -> str:
    """Strip `./` and `/` prefixes from an archive path, rejecting `..` components."""
    while path.startswith("./"):
        path = path[2:]
    path = path.strip("/")
    if path == ".":
        return ""
    if ".." in path.split("/"):
        raise DebError(f"refusing path outside the image root: {path}")
    return path


def _merge_usr(path: str, merged_usr: bool) -> str:
    if merged_usr and path:
        top, _, rest = path.partition("/")
        if top in MERGED_USR_DIRS:
            return f"usr/{top}/{rest}" if rest else ""
    return path


def _normalize(path: str, merged_usr: bool) -> str:
    return _merge_usr(clean_path(path), merged_usr)


def _symlink_target(original: str, linkname: str, merged_usr: bool) -> str:
    """Keep a relative symlink pointing at the same file after its path is rewritten for merged-/usr.

    `original` is the cleaned path of the link before rewriting. Absolute targets are left alone,
    since the base image's own `/bin -> usr/bin` style links resolve them.
    """
    if linkname.startswith("/") or not merged_usr:
        return linkname

    # Resolve against the image root; `..` above the root stays at the root, like the kernel does.
    resolved = posixpath.normpath(posixpath.join("/", posixpath.dirname(original), linkname))
    target = _merge_usr(resolved.lstrip("/"), merged_usr)
    path = _merge_usr(original, merged_usr)
    if path == original and target == resolved.lstrip("/"):
        return linkname
    return posixpath.relpath(target or ".", posixpath.dirname(path) or ".")


def _rewrite_md5sums(md5sums: bytes, merged_usr: bool) -> bytes:
    """Rewrite the paths in an `md5sums` file to where the files end up in the layer."""
    lines = []
    for line in md5sums.decode("utf-8").split("\n"):
        digest, sep, path = line.partition("  ")
        lines.append(f"{digest}{sep}{_normalize(path, merged_usr)}" if sep else line)
    return "\n".join(lines).encode("utf-8")


def directory_info(
    name: str, mode: int = 0o755, uid: int = 0, gid: int = 0, uname: str = "root", gname: str = "root"
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.uid, info.gid = uid, gid
    info.uname, info.gname = uname, gname
    return info


@dataclass(frozen=True)
class _Entry:
    """What was written to the layer at a path, to compare against later packages shipping it."""

    owner: str
    type: bytes
    mode: int
    uid: int
    gid: int
    uname: str
    gname: str
    linkname: str
    devmajor: int
    devminor: int
    sha256: str | None

    @classmethod
    def of(cls, info: tarfile.TarInfo, owner: str, sha256: str | None) -> _Entry:
        return cls(
            owner=owner,
            type=info.type,
            mode=info.mode,
            uid=info.uid,
            gid=info.gid,
            uname=info.uname,
            gname=info.gname,
            linkname=info.linkname,
            devmajor=info.devmajor,
            devminor=info.devminor,
            sha256=sha256,
        )

    def differences(self, other: _Entry) -> list[str]:
        return [
            "content" if f.name == "sha256" else f.name
            for f in dataclass_fields(self)
            if f.name != "owner" and getattr(self, f.name) != getattr(other, f.name)
        ]


def _sha256(f: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := f.read(1 << 16):
        digest.update(chunk)
    return digest.hexdigest()


def file_info(name: str, content: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    info.uname = info.gname = "root"
    return info


def build_layer(packages: list[DebPackage], out: io.BufferedIOBase, merged_usr: bool = True) -> None:
    """Write a deterministic layer tarball containing `packages`.

    Packages are written in name order and members in their original archive order, so hardlinks
    always follow their targets. Timestamps are zeroed; ownership and modes are kept from the deb.
    Each package's `data.tar` is streamed straight from its deb file.
    """
    seen: dict[str, _Entry] = {}
    by_name: dict[str, DebPackage] = {}
    architectures: dict[str, list[str]] = {}
    for package in packages:
        architecture = dict(parse_control(package.control)).get("Architecture", "all")
        if architecture != "all":
            architectures.setdefault(architecture, []).append(package.name)
    if len(architectures) > 1:
        raise DebError(
            "packages for more than one architecture: "
            + "; ".join(
                f"{arch}: {', '.join(sorted(names))}" for arch, names in sorted(architectures.items())
            )
        )
    for package in packages:
        if package.name in by_name:
            raise DebError(
                f"package {package.name} given twice: {by_name[package.name].source} and {package.source}"
            )
        by_name[package.name] = package

    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as layer:

        def add(info: tarfile.TarInfo, owner: str, content: BinaryIO | None = None) -> None:
            info.mtime = 0
            previous = seen.get(info.name)
            if previous is not None:
                # Shared directories keep the metadata of the first package, in name order.
                if info.isdir() and previous.type == tarfile.DIRTYPE:
                    return
                # Other shared paths must be identical in every package, as dpkg requires.
                entry = _Entry.of(info, owner, _sha256(content) if content is not None else None)
                differences = previous.differences(entry)
                if differences:
                    raise DebError(
                        f"{info.name} is provided by both {previous.owner} and {owner} "
                        f"({', '.join(differences)} differ)"
                    )
                return
            if info.islnk():
                target = seen.get(info.linkname)
                if target is None or target.type == tarfile.DIRTYPE:
                    raise DebError(
                        f"{info.name} in {owner} is a hardlink to {info.linkname}, which is not a "
                        "file earlier in the layer"
                    )
            hashing = _HashingReader(content) if content is not None else None
            layer.addfile(info, hashing)
            seen[info.name] = _Entry.of(info, owner, hashing.sha256.hexdigest() if hashing else None)

        for name in sorted(by_name):
            package = by_name[name]
            with open_data(package) as data:
                for member in data:
                    original = clean_path(member.name)
                    path = _merge_usr(original, merged_usr)
                    if not path:
                        continue

                    xattrs = sorted(k for k in member.pax_headers if k.startswith(XATTR_HEADERS))
                    if xattrs:
                        raise DebError(
                            f"{path} in {name} has extended attributes ({', '.join(xattrs)}), "
                            "which deb layers do not support"
                        )

                    info = tarfile.TarInfo(path)
                    info.type = member.type
                    info.mode = member.mode
                    info.uid, info.gid = member.uid, member.gid
                    info.uname, info.gname = member.uname, member.gname
                    info.devmajor, info.devminor = member.devmajor, member.devminor
                    if member.islnk():
                        info.linkname = _normalize(member.linkname, merged_usr)
                    elif member.issym():
                        info.linkname = _symlink_target(original, member.linkname, merged_usr)

                    content = None
                    if member.isreg():
                        content = data.extractfile(member)
                        info.size = member.size

                    add(info, name, content)

        parts = STATUS_DIR.split("/")
        for depth in range(1, len(parts) + 1):
            add(directory_info("/".join(parts[:depth])), "status.d")

        for name in sorted(by_name):
            package = by_name[name]
            entry = status_entry(package.control)
            add(file_info(f"{STATUS_DIR}/{name}", entry), name, io.BytesIO(entry))
            if package.md5sums is not None:
                md5sums = _rewrite_md5sums(package.md5sums, merged_usr)
                add(file_info(f"{STATUS_DIR}/{name}.md5sums", md5sums), name, io.BytesIO(md5sums))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-merged-usr", dest="merged_usr", action="store_false")
    parser.add_argument("--zstd", help="A zstd binary, for zstd-compressed members.")
    parser.add_argument("debs", nargs="+")
    args = parser.parse_args(argv)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    try:
        with contextlib.ExitStack() as stack:
            packages = [
                load_deb(stack.enter_context(open(path, "rb")), source=path, zstd=args.zstd)
                for path in args.debs
            ]
            with open(args.output, "wb") as out:
                build_layer(packages, out, merged_usr=args.merged_usr)
    except DebError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
