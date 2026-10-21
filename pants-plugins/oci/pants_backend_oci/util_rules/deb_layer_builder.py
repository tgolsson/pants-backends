# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Builds an OCI layer tarball from a set of `.deb` packages without running dpkg.

The package contents (`data.tar.*`) are copied into the layer, and each package gets a
`/var/lib/dpkg/status.d/<package>` control file (plus `<package>.md5sums`) in the format used by
distroless images, which is what scanners such as Trivy, Syft and Grype read. Maintainer scripts are
never executed.

This module only uses the standard library: the `oci_deb_layer` rules copy it into the sandbox and
call `main`, and tests import it directly.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import io
import lzma
import os
import posixpath
import re
import sys
import tarfile
from dataclasses import dataclass, field

STATUS_DIR = "var/lib/dpkg/status.d"

# Top-level directories that are symlinks into `/usr` on a merged-/usr system. dpkg follows those
# symlinks when unpacking; a layer can't, so paths are rewritten instead.
MERGED_USR_DIRS = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")

# Debian policy 5.6.1: package names are lowercase alphanumerics plus `+`, `-` and `.`.
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]+")


class DebError(Exception):
    pass


@dataclass(frozen=True)
class DebPackage:
    name: str
    control: bytes
    md5sums: bytes | None
    data: bytes = field(repr=False)
    source: str = ""


def read_ar(content: bytes) -> dict[str, bytes]:
    """Read the members of an `ar` archive, as used by the `.deb` format."""
    if not content.startswith(b"!<arch>\n"):
        raise DebError("not an ar archive")

    members = {}
    offset = 8
    while offset < len(content):
        header = content[offset : offset + 60]
        if len(header) < 60 or header[58:60] != b"`\n":
            raise DebError(f"malformed ar header at offset {offset}")

        name = header[0:16].decode("ascii").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            raise DebError(f"malformed ar member size at offset {offset}") from None
        start = offset + 60
        if start + size > len(content):
            raise DebError(f"ar member {name} is truncated")
        members[name] = content[start : start + size]
        offset = start + size + (size % 2)

    return members


def _decompress(name: str, payload: bytes) -> bytes:
    if name.endswith(".tar"):
        return payload
    if name.endswith(".tar.gz"):
        return gzip.decompress(payload)
    if name.endswith(".tar.xz"):
        return lzma.decompress(payload)
    if name.endswith(".tar.bz2"):
        return bz2.decompress(payload)
    if name.endswith(".tar.zst"):
        raise DebError(f"{name}: zstd-compressed members are not supported yet")
    raise DebError(f"{name}: unknown compression")


def _find_member(members: dict[str, bytes], prefix: str) -> tuple[str, bytes]:
    for name, payload in members.items():
        if name.startswith(prefix):
            return name, payload
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


def load_deb(content: bytes, source: str = "") -> DebPackage:
    try:
        members = read_ar(content)
    except DebError as e:
        raise DebError(f"{source}: {e}") from e

    if members.get("debian-binary", b"").strip() != b"2.0":
        raise DebError(f"{source}: unsupported deb format")

    control_name, control_payload = _find_member(members, "control.tar")
    data_name, data_payload = _find_member(members, "data.tar")

    with tarfile.open(fileobj=io.BytesIO(_decompress(control_name, control_payload))) as tar:
        control = _read_tar_file(tar, "control")
        md5sums = _read_tar_file(tar, "md5sums")

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
        data=_decompress(data_name, data_payload),
        source=source,
    )


def status_entry(control: bytes) -> bytes:
    """Render a package's control paragraph as an installed dpkg status entry."""
    fields = [(k, v) for k, v in parse_control(control) if k != "Status"]
    rendered = []
    for key, value in fields:
        rendered.append(f"{key}: {value}")
        if key == "Package":
            rendered.append("Status: install ok installed")
    return ("\n".join(rendered) + "\n").encode("utf-8")


def _clean(path: str) -> str:
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
    return _merge_usr(_clean(path), merged_usr)


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


def _directory(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.uname = info.gname = "root"
    return info


def _regular_file(name: str, content: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    info.uname = info.gname = "root"
    return info


def build_layer(packages: list[DebPackage], out: io.BufferedIOBase, merged_usr: bool = True) -> None:
    """Write a deterministic layer tarball containing `packages`.

    Packages are written in name order and members in their original archive order, so hardlinks
    always follow their targets. Timestamps are zeroed; ownership and modes are kept from the deb.
    """
    # Path -> (owning package, whether it is a directory).
    seen: dict[str, tuple[str, bool]] = {}
    by_name: dict[str, DebPackage] = {}
    for package in packages:
        if package.name in by_name:
            raise DebError(
                f"package {package.name} given twice: {by_name[package.name].source} and {package.source}"
            )
        by_name[package.name] = package

    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as layer:

        def add(info: tarfile.TarInfo, owner: str, content: bytes | None = None) -> None:
            info.mtime = 0
            if info.name in seen:
                previous, previous_is_dir = seen[info.name]
                # Shared directories keep the metadata of the first package, in name order.
                if info.isdir() and previous_is_dir:
                    return
                raise DebError(f"{info.name} is provided by both {previous} and {owner}")
            if info.islnk():
                target = seen.get(info.linkname)
                if target is None or target[1]:
                    raise DebError(
                        f"{info.name} in {owner} is a hardlink to {info.linkname}, which is not a "
                        "file earlier in the layer"
                    )
            seen[info.name] = (owner, info.isdir())
            layer.addfile(info, io.BytesIO(content) if content is not None else None)

        for name in sorted(by_name):
            package = by_name[name]
            with tarfile.open(fileobj=io.BytesIO(package.data)) as data:
                for member in data:
                    original = _clean(member.name)
                    path = _merge_usr(original, merged_usr)
                    if not path:
                        continue

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
                        extracted = data.extractfile(member)
                        content = extracted.read() if extracted else b""
                        info.size = len(content)

                    add(info, name, content)

        parts = STATUS_DIR.split("/")
        for depth in range(1, len(parts) + 1):
            add(_directory("/".join(parts[:depth])), "status.d")

        for name in sorted(by_name):
            package = by_name[name]
            entry = status_entry(package.control)
            add(_regular_file(f"{STATUS_DIR}/{name}", entry), name, entry)
            if package.md5sums is not None:
                md5sums = _rewrite_md5sums(package.md5sums, merged_usr)
                add(_regular_file(f"{STATUS_DIR}/{name}.md5sums", md5sums), name, md5sums)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-merged-usr", dest="merged_usr", action="store_false")
    parser.add_argument("debs", nargs="+")
    args = parser.parse_args(argv)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    try:
        packages = []
        for path in args.debs:
            with open(path, "rb") as f:
                packages.append(load_deb(f.read(), source=path))

        with open(args.output, "wb") as out:
            build_layer(packages, out, merged_usr=args.merged_usr)
    except DebError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
