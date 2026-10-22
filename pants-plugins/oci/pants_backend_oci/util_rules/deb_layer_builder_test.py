from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import os
import struct
import sys
import tarfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

from pants_backend_oci.util_rules.deb_layer_builder import (
    STATUS_DIR,
    DebError,
    DebPackage,
    build_layer,
    load_deb,
    open_data,
    parse_control,
    read_ar,
    status_entry,
)


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, payload in members:
        header = f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(payload):<10}`\n"
        out += header.encode("ascii") + payload
        if len(payload) % 2:
            out += b"\n"
    return bytes(out)


def _tar(
    entries: list[tarfile.TarInfo | tuple[str, bytes]],
    mtime: int = 1700000000,
    format: int = tarfile.GNU_FORMAT,
) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=format) as tar:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                entry.mtime = mtime
                tar.addfile(entry)
                continue
            name, content = entry
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if "/bin/" in name else 0o644
            info.mtime = mtime
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZSTD_MAX_BLOCK = 128 * 1024


def zstd_frame(data: bytes) -> bytes:
    """A valid zstd frame storing `data` in raw (uncompressed) blocks, so tests need no zstd library.

    The frame header is a single segment with an 8-byte content size and no checksum.
    """
    out = bytearray(ZSTD_MAGIC + bytes([0b1110_0000]) + struct.pack("<Q", len(data)))
    chunks = [data[i : i + ZSTD_MAX_BLOCK] for i in range(0, len(data), ZSTD_MAX_BLOCK)] or [b""]
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        out += struct.pack("<I", (len(chunk) << 3) | last)[:3] + chunk
    return bytes(out)


# Decodes the raw-block frames `zstd_frame` writes, standing in for `zstd -d -c -q`.
FAKE_ZSTD = f"""#!{sys.executable}
import sys

# Like zstd, write each block as soon as it is decoded, so a failure can follow partial output.
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
if stdin.read(4) != {ZSTD_MAGIC!r}:
    sys.exit("zstd: /*stdin*\\\\: unknown header")
stdin.read(9)
while True:
    header = int.from_bytes(stdin.read(3), "little")
    block = stdin.read(header >> 3)
    if len(block) < header >> 3:
        sys.exit("zstd: /*stdin*\\\\: truncated input")
    stdout.write(block)
    stdout.flush()
    if header & 1:
        break
"""


@pytest.fixture
def fake_zstd(tmp_path: Path) -> str:
    path = tmp_path / "zstd"
    path.write_text(FAKE_ZSTD)
    path.chmod(0o755)
    return str(path)


def _compress(member: str, data: bytes) -> bytes:
    if member.endswith(".xz"):
        return lzma.compress(data)
    if member.endswith(".gz"):
        return gzip.compress(data, mtime=0)
    if member.endswith(".bz2"):
        return bz2.compress(data)
    if member.endswith(".zst"):
        return zstd_frame(data)
    return data


def _dir(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


def _link(name: str, target: str, kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


def make_deb(
    package: str,
    data: list[tarfile.TarInfo | tuple[str, bytes]],
    *,
    extra_control: str = "",
    architecture: str = "amd64",
    md5sums: bytes | None = b"",
    data_member: str = "data.tar.xz",
    control_member: str = "control.tar.xz",
    mtime: int = 1700000000,
    data_format: int = tarfile.GNU_FORMAT,
    mangle: Callable[[bytes], bytes] | None = None,
) -> bytes:
    control = (
        f"Package: {package}\nVersion: 1.0-1\nArchitecture: {architecture}\n{extra_control}"
        f"Description: test package\n long description\n .\n more\n"
    ).encode()
    control_entries: list[tarfile.TarInfo | tuple[str, bytes]] = [_dir("./"), ("./control", control)]
    if md5sums is not None:
        control_entries.append(("./md5sums", md5sums or f"abc  usr/bin/{package}\n".encode()))

    data_tar = _compress(data_member, _tar([_dir("./"), *data], mtime=mtime, format=data_format))
    if mangle is not None:
        data_tar = mangle(data_tar)

    return _ar(
        [
            ("debian-binary", b"2.0\n"),
            (control_member, _compress(control_member, _tar(control_entries, mtime=mtime))),
            (data_member, data_tar),
        ]
    )


def load(deb: bytes, source: str = "", zstd: str | None = None) -> DebPackage:
    return load_deb(io.BytesIO(deb), source=source, zstd=zstd)


def layer_bytes(packages, **kwargs) -> bytes:
    out = io.BytesIO()
    build_layer(packages, out, **kwargs)
    return out.getvalue()


def layer_members(layer: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(layer)) as tar:
        return {m.name: m for m in tar.getmembers()}


def read_layer_file(layer: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(layer)) as tar:
        extracted = tar.extractfile(name)
        assert extracted is not None
        return extracted.read()


FOO = make_deb("foo", [_dir("./usr/"), _dir("./usr/bin/"), ("./usr/bin/foo", b"#!/bin/sh\necho foo\n")])
BAR = make_deb(
    "bar",
    [_dir("./usr/"), _dir("./usr/lib/"), ("./usr/lib/libbar.so.1.0", b"\x7fELF")],
    extra_control="Depends: foo (>= 1.0)\n",
)


def test_read_ar_pads_odd_members() -> None:
    archive = _ar([("a", b"odd"), ("b", b"even")])
    members = read_ar(io.BytesIO(archive))
    assert members == {"a": (68, 3), "b": (132, 4)}
    assert archive[68:71] == b"odd" and archive[132:136] == b"even"


def test_read_ar_rejects_non_archives() -> None:
    with pytest.raises(DebError, match="not an ar archive"):
        read_ar(io.BytesIO(b"PK\x03\x04"))


def test_load_deb_reads_control_and_md5sums() -> None:
    package = load(FOO, source="foo.deb")
    assert package.name == "foo"
    assert package.md5sums == b"abc  usr/bin/foo\n"
    assert dict(parse_control(package.control))["Version"] == "1.0-1"


FOO_DATA = [_dir("./usr/"), _dir("./usr/bin/"), ("./usr/bin/foo", b"#!/bin/sh\necho foo\n")]


def test_load_deb_needs_zstd_binary_for_zstd_members() -> None:
    deb = make_deb("foo", FOO_DATA, data_member="data.tar.zst")
    with pytest.raises(
        DebError, match="foo.deb: data.tar.zst: reading zstd-compressed members needs a zstd binary"
    ):
        load(deb, source="foo.deb")


def test_zstd_frame_splits_large_data_into_blocks(fake_zstd: str) -> None:
    deb = make_deb("foo", [("./usr/bin/foo", os.urandom(300 * 1024))], data_member="data.tar.zst")
    package = load(deb, source="foo.deb", zstd=fake_zstd)
    layer = layer_bytes([package])
    assert len(read_layer_file(layer, "usr/bin/foo")) == 300 * 1024


def test_zstd_deb_gives_same_layer_as_xz(fake_zstd: str) -> None:
    zst = make_deb("foo", FOO_DATA, data_member="data.tar.zst", control_member="control.tar.zst")
    xz = make_deb("foo", FOO_DATA)
    package = load(zst, source="foo.deb", zstd=fake_zstd)
    assert package.name == "foo" and package.md5sums == b"abc  usr/bin/foo\n"
    assert layer_bytes([package]) == layer_bytes([load(xz)])


def test_layer_rejects_corrupt_zstd_data(fake_zstd: str) -> None:
    deb = make_deb("foo", FOO_DATA, data_member="data.tar.zst", mangle=lambda data: data[:-10])
    with pytest.raises(DebError, match=r"broken.deb: data.tar.zst is corrupt: zstd: truncated input$"):
        layer_bytes([load(deb, source="broken.deb", zstd=fake_zstd)])


def test_load_deb_rejects_corrupt_zstd_control(fake_zstd: str) -> None:
    deb = make_deb("foo", FOO_DATA, control_member="control.tar.zst")
    deb = deb.replace(ZSTD_MAGIC, b"\0\0\0\0", 1)
    with pytest.raises(DebError, match=r"broken.deb: control.tar.zst is corrupt: zstd: unknown header"):
        load(deb, source="broken.deb", zstd=fake_zstd)


def test_load_deb_reports_missing_zstd_binary(tmp_path: Path) -> None:
    deb = make_deb("foo", FOO_DATA, control_member="control.tar.zst")
    with pytest.raises(DebError, match="could not run"):
        load(deb, source="foo.deb", zstd=str(tmp_path / "missing"))


def _unfinished(frame: bytes) -> bytes:
    """Mark a single-block frame's block as not the last, and follow it with a truncated block."""
    return frame[:13] + bytes([frame[13] & ~1]) + frame[14:] + struct.pack("<I", 100 << 3)[:3] + b"abc"


def test_zstd_failure_after_a_complete_tar_is_reported(fake_zstd: str) -> None:
    # The tar ends in the first block, so only zstd's exit status shows the stream is broken.
    deb = make_deb("foo", FOO_DATA, data_member="data.tar.zst", mangle=_unfinished)
    with pytest.raises(DebError, match=r"broken.deb: data.tar.zst is corrupt: zstd: truncated input$"):
        layer_bytes([load(deb, source="broken.deb", zstd=fake_zstd)])


def test_zstd_is_stopped_when_the_reader_fails(fake_zstd: str) -> None:
    deb = make_deb("foo", [("./usr/bin/foo", os.urandom(1 << 20))], data_member="data.tar.zst")
    package = load(deb, source="foo.deb", zstd=fake_zstd)
    threads = threading.active_count()
    with pytest.raises(RuntimeError, match="stop"):
        with open_data(package) as data:
            next(iter(data))
            raise RuntimeError("stop")
    assert threading.active_count() == threads


class _FailingFile(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        raise OSError("disk on fire")


def test_zstd_reports_read_errors_of_the_deb(fake_zstd: str) -> None:
    deb = make_deb("foo", FOO_DATA, data_member="data.tar.zst")
    package = replace(load(deb, source="foo.deb", zstd=fake_zstd), fileobj=_FailingFile(deb))
    with pytest.raises(DebError, match="foo.deb: data.tar.zst: could not read the package: disk on fire"):
        layer_bytes([package])


def test_load_deb_rejects_corrupt_control_tar() -> None:
    deb = _ar([("debian-binary", b"2.0\n"), ("control.tar", b"garbage"), ("data.tar", _tar([]))])
    with pytest.raises(DebError, match="broken.deb: control.tar is corrupt"):
        load(deb, source="broken.deb")


def test_parse_control_keeps_continuation_lines() -> None:
    fields = dict(parse_control(load(FOO).control))
    assert fields["Description"] == "test package\n long description\n .\n more"


def test_status_entry_marks_package_installed() -> None:
    control = b"Package: foo\nStatus: purge ok not-installed\nVersion: 1.0-1\nDescription: x\n y\n"
    assert status_entry(control) == (
        b"Package: foo\nStatus: install ok installed\nVersion: 1.0-1\nDescription: x\n y\n"
    )


def test_layer_contains_contents_and_status() -> None:
    layer = layer_bytes([load(FOO), load(BAR)])
    members = layer_members(layer)

    assert read_layer_file(layer, "usr/bin/foo") == b"#!/bin/sh\necho foo\n"
    assert members["usr/bin/foo"].mode == 0o755
    assert read_layer_file(layer, "usr/lib/libbar.so.1.0") == b"\x7fELF"
    assert members[STATUS_DIR].isdir()
    assert all(m.mtime == 0 for m in members.values())

    bar_status = read_layer_file(layer, f"{STATUS_DIR}/bar").decode()
    assert bar_status.startswith("Package: bar\nStatus: install ok installed\nVersion: 1.0-1\n")
    assert "Depends: foo (>= 1.0)\n" in bar_status
    assert read_layer_file(layer, f"{STATUS_DIR}/foo.md5sums") == b"abc  usr/bin/foo\n"


def test_layer_skips_md5sums_when_package_has_none() -> None:
    deb = make_deb("nosums", [("./usr/share/nosums", b"")], md5sums=None)
    members = layer_members(layer_bytes([load(deb)]))
    assert f"{STATUS_DIR}/nosums" in members
    assert f"{STATUS_DIR}/nosums.md5sums" not in members


def test_layer_is_reproducible() -> None:
    first = layer_bytes([load(FOO), load(BAR)])
    # Same packages in another order and with different archive timestamps.
    foo_later = make_deb(
        "foo",
        [_dir("./usr/"), _dir("./usr/bin/"), ("./usr/bin/foo", b"#!/bin/sh\necho foo\n")],
        mtime=1800000000,
    )
    second = layer_bytes([load(BAR), load(foo_later)])
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_layer_rewrites_merged_usr_paths() -> None:
    deb = make_deb(
        "tool",
        [
            _dir("./bin/"),
            ("./bin/tool", b"tool"),
            _link("./bin/tool-hard", "./bin/tool", tarfile.LNKTYPE),
            _link("./sbin/tool", "/bin/tool", tarfile.SYMTYPE),
            _dir("./lib/"),
            ("./lib/libtool.so", b"lib"),
        ],
    )
    members = layer_members(layer_bytes([load(deb)]))

    assert {"bin", "sbin", "lib"}.isdisjoint(members)
    assert "usr/bin/tool" in members
    assert "usr/lib/libtool.so" in members
    assert members["usr/bin/tool-hard"].linkname == "usr/bin/tool"
    # Symlink targets resolve through the base image's own /bin -> usr/bin link.
    assert members["usr/sbin/tool"].linkname == "/bin/tool"


def test_layer_keeps_split_usr_paths_when_disabled() -> None:
    deb = make_deb("tool", [_dir("./bin/"), ("./bin/tool", b"tool")])
    members = layer_members(layer_bytes([load(deb)], merged_usr=False))
    assert members["bin"].isdir()
    assert "bin/tool" in members


def test_layer_rejects_file_conflicts() -> None:
    other = make_deb("other", [_dir("./usr/"), _dir("./usr/bin/"), ("./usr/bin/foo", b"other")])
    with pytest.raises(DebError, match="usr/bin/foo is provided by both foo and other"):
        layer_bytes([load(FOO), load(other)])


def test_layer_rejects_duplicate_packages() -> None:
    with pytest.raises(DebError, match="package foo given twice"):
        layer_bytes([load(FOO, "a.deb"), load(FOO, "b.deb")])


def test_layer_rejects_mixed_architectures() -> None:
    arm = make_deb("bar", [], architecture="arm64")
    with pytest.raises(DebError, match="more than one architecture: amd64: foo; arm64: bar"):
        layer_bytes([load(FOO, "a.deb"), load(arm, "b.deb")])

    # Architecture-independent packages go with any architecture.
    layer_bytes([load(FOO, "a.deb"), load(make_deb("doc", [], architecture="all"), "b.deb")])


def test_read_ar_rejects_truncated_members() -> None:
    archive = _ar([("debian-binary", b"2.0\n")])
    with pytest.raises(DebError, match="debian-binary is truncated"):
        read_ar(io.BytesIO(archive[:-2]))


def test_parse_control_only_splits_on_newlines() -> None:
    control = "Package: foo\nDescription: a b\n more\r\n".encode()
    assert dict(parse_control(control))["Description"] == "a b\n more"


def test_load_deb_rejects_invalid_package_names() -> None:
    with pytest.raises(DebError, match="invalid package name '../evil'"):
        load(make_deb("../evil", [("./usr/share/x", b"")]))


def test_layer_rejects_paths_outside_root() -> None:
    deb = make_deb("evil", [("./usr/../../etc/passwd", b"root::0:0::/:/bin/sh\n")])
    with pytest.raises(DebError, match="outside the image root"):
        layer_bytes([load(deb)])


def test_layer_rewrites_relative_symlinks_for_merged_usr() -> None:
    deb = make_deb(
        "links",
        [
            _dir("./lib/"),
            _dir("./lib/x86_64-linux-gnu/"),
            ("./lib/x86_64-linux-gnu/libfoo.so.1", b"lib"),
            # Stays inside the moved directory: unchanged.
            _link("./lib/x86_64-linux-gnu/libfoo.so", "libfoo.so.1", tarfile.SYMTYPE),
            # Climbs out of the moved directory.
            _link("./lib/x86_64-linux-gnu/libbar.so", "../../usr/lib/libbar.so.2", tarfile.SYMTYPE),
            _dir("./bin/"),
            _link("./bin/y", "../usr/bin/x", tarfile.SYMTYPE),
            # Not moved itself, but points into a moved directory.
            _dir("./usr/"),
            _dir("./usr/lib/"),
            _link("./usr/lib/libfoo.so", "../../lib/x86_64-linux-gnu/libfoo.so.1", tarfile.SYMTYPE),
        ],
    )
    members = layer_members(layer_bytes([load(deb)]))

    assert members["usr/lib/x86_64-linux-gnu/libfoo.so"].linkname == "libfoo.so.1"
    assert members["usr/lib/x86_64-linux-gnu/libbar.so"].linkname == "../libbar.so.2"
    assert members["usr/bin/y"].linkname == "x"
    assert members["usr/lib/libfoo.so"].linkname == "x86_64-linux-gnu/libfoo.so.1"


def test_layer_rejects_directory_replacing_file() -> None:
    other = make_deb("other", [_dir("./usr/"), _dir("./usr/bin/"), _dir("./usr/bin/foo/")])
    with pytest.raises(DebError, match="usr/bin/foo is provided by both foo and other"):
        layer_bytes([load(FOO), load(other)])


def test_layer_rejects_dangling_hardlinks() -> None:
    deb = make_deb(
        "dangling",
        [_link("./usr/bin/a", "./usr/bin/b", tarfile.LNKTYPE), ("./usr/bin/b", b"b")],
    )
    with pytest.raises(DebError, match="usr/bin/a in dangling is a hardlink to usr/bin/b"):
        layer_bytes([load(deb)])


def test_layer_rewrites_md5sums_for_merged_usr() -> None:
    deb = make_deb(
        "tool",
        [_dir("./bin/"), ("./bin/tool", b"tool")],
        md5sums=b"aaa  bin/tool\nbbb  usr/share/doc/tool/copyright\n",
    )
    layer = layer_bytes([load(deb)])
    assert read_layer_file(layer, f"{STATUS_DIR}/tool.md5sums") == (
        b"aaa  usr/bin/tool\nbbb  usr/share/doc/tool/copyright\n"
    )

    layer = layer_bytes([load(deb)], merged_usr=False)
    assert read_layer_file(layer, f"{STATUS_DIR}/tool.md5sums").startswith(b"aaa  bin/tool\n")


def _shared(package: str, *entries: tarfile.TarInfo | tuple[str, bytes]) -> bytes:
    return make_deb(package, [_dir("./usr/"), _dir("./usr/share/"), *entries])


def test_layer_accepts_identical_shared_files() -> None:
    a = _shared("pkga", ("./usr/share/common", b"same"), ("./usr/share/a", b"a"))
    b = _shared("pkgb", ("./usr/share/common", b"same"), ("./usr/share/b", b"b"))
    layer = layer_bytes([load(a), load(b)])

    with tarfile.open(fileobj=io.BytesIO(layer)) as tar:
        names = [m.name for m in tar.getmembers()]
    assert names.count("usr/share/common") == 1
    assert read_layer_file(layer, "usr/share/common") == b"same"
    assert {"usr/share/a", "usr/share/b"} <= set(names)


def test_layer_accepts_identical_shared_symlinks() -> None:
    a = _shared("pkga", _link("./usr/share/link", "target", tarfile.SYMTYPE))
    b = _shared("pkgb", _link("./usr/share/link", "target", tarfile.SYMTYPE))
    members = layer_members(layer_bytes([load(a), load(b)]))
    assert members["usr/share/link"].linkname == "target"


def test_layer_accepts_hardlink_to_shared_file() -> None:
    a = _shared("pkga", ("./usr/share/common", b"same"))
    b = _shared(
        "pkgb",
        ("./usr/share/common", b"same"),
        _link("./usr/share/hard", "./usr/share/common", tarfile.LNKTYPE),
    )
    members = layer_members(layer_bytes([load(a), load(b)]))
    assert members["usr/share/hard"].linkname == "usr/share/common"


def test_layer_rejects_shared_files_with_different_mode() -> None:
    executable = tarfile.TarInfo("./usr/share/common")
    executable.mode = 0o755
    a = _shared("pkga", ("./usr/share/common", b""))
    b = _shared("pkgb", executable)
    with pytest.raises(DebError, match=r"usr/share/common is provided by both pkga and pkgb \(mode differ\)"):
        layer_bytes([load(a), load(b)])


def test_layer_rejects_shared_files_with_different_content() -> None:
    a = _shared("pkga", ("./usr/share/common", b"same"))
    b = _shared("pkgb", ("./usr/share/common", b"diff"))
    with pytest.raises(
        DebError, match=r"usr/share/common is provided by both pkga and pkgb \(content differ\)"
    ):
        layer_bytes([load(a), load(b)])


def test_layer_rejects_symlink_shadowing_file() -> None:
    a = _shared("pkga", ("./usr/share/common", b"target"))
    b = _shared("pkgb", _link("./usr/share/common", "target", tarfile.SYMTYPE))
    with pytest.raises(DebError, match=r"usr/share/common is provided by both pkga and pkgb \(type, "):
        layer_bytes([load(a), load(b)])


def test_layer_rejects_extended_attributes() -> None:
    info = tarfile.TarInfo("./usr/bin/ping")
    info.pax_headers = {"SCHILY.xattr.security.capability": "\x01\x00\x00\x02"}
    deb = make_deb("ping", [info], data_format=tarfile.PAX_FORMAT)
    with pytest.raises(DebError, match=r"usr/bin/ping in ping has extended attributes \(SCHILY"):
        layer_bytes([load(deb)])


@pytest.mark.parametrize("data_member", ["data.tar", "data.tar.gz", "data.tar.xz", "data.tar.bz2"])
def test_layer_streams_each_compression(data_member: str) -> None:
    content = bytes(range(256)) * 1024
    deb = make_deb("big", [("./usr/share/big", content)], data_member=data_member)
    assert read_layer_file(layer_bytes([load(deb)]), "usr/share/big") == content


def test_layer_accepts_identical_shared_symlinks_after_merged_usr_rewrite() -> None:
    a = make_deb("pkga", [_dir("./bin/"), _link("./bin/foo", "bar", tarfile.SYMTYPE)])
    b = make_deb("pkgb", [_dir("./usr/"), _dir("./usr/bin/"), _link("./usr/bin/foo", "bar", tarfile.SYMTYPE)])
    members = layer_members(layer_bytes([load(a), load(b)]))
    assert members["usr/bin/foo"].linkname == "bar"


def test_layer_rejects_shared_files_with_different_owner() -> None:
    owned = tarfile.TarInfo("./usr/share/common")
    owned.mode = 0o644
    owned.uname = "nobody"
    a = _shared("pkga", ("./usr/share/common", b""))
    b = _shared("pkgb", owned)
    with pytest.raises(DebError, match=r"provided by both pkga and pkgb \(uname differ\)"):
        layer_bytes([load(a), load(b)])


def _corrupt_gzip_crc(data: bytes) -> bytes:
    # The gzip trailer is CRC32 then length; the tar itself still decompresses fine.
    return data[:-8] + bytes(b ^ 0xFF for b in data[-8:-4]) + data[-4:]


@pytest.mark.parametrize(
    ("data_member", "mangle"),
    [
        ("data.tar.gz", _corrupt_gzip_crc),
        # Only the xz index and footer are missing.
        ("data.tar.xz", lambda data: data[:-12]),
        ("data.tar.xz", lambda data: data[:-40]),
        ("data.tar.gz", lambda data: data[: len(data) // 2]),
    ],
)
def test_layer_rejects_corrupt_data(data_member: str, mangle: Callable[[bytes], bytes]) -> None:
    deb = make_deb(
        "broken", [("./usr/share/broken", bytes(range(256)) * 64)], data_member=data_member, mangle=mangle
    )
    with pytest.raises(DebError, match=rf"broken.deb: {data_member} is corrupt"):
        layer_bytes([load(deb, "broken.deb")])
