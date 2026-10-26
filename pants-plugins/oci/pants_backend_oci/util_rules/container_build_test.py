# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import container_build
from pants_backend_oci.util_rules import oci_layout_builder as b

RUN = ["/bin/sh", "-c", "echo hi > /out/hi"]


def test_containerfile_runs_as_root_then_copies_what_outputs_need():
    outputs = ["bin/app", "./etc/x/", "lib/*.so", "usr/*/libfoo.so", "usr/share/doc"]
    assert container_build.containerfile("oci-archive:/s/image.tar:ref", RUN, outputs) == (
        "FROM oci-archive:/s/image.tar:ref AS build\n"
        "USER 0:0\n"
        'RUN ["/bin/sh", "-c", "echo hi > /out/hi"]\n'
        "FROM scratch\n"
        'COPY --from=build ["/bin/app", "/bin/app"]\n'
        'COPY --from=build ["/etc/x", "/etc/x"]\n'
        'COPY --from=build ["/lib", "/lib"]\n'
        'COPY --from=build ["/usr", "/usr"]\n'
    )


def test_containerfile_without_outputs_keeps_the_built_image():
    assert container_build.containerfile("oci-archive:/s/image.tar:ref", RUN, []) == (
        'FROM oci-archive:/s/image.tar:ref AS build\nUSER 0:0\nRUN ["/bin/sh", "-c", "echo hi > /out/hi"]\n'
    )


def test_containerfile_for_extract_runs_nothing():
    containerfile = container_build.containerfile("oci-archive:/s/image.tar:ref", None, ["bin"])
    assert "RUN" not in containerfile and "USER" not in containerfile
    assert 'COPY --from=build ["/bin", "/bin"]' in containerfile


@pytest.mark.parametrize(
    "name,selected",
    [
        ("opt/app", True),
        ("opt/app/bin/x", True),
        ("opt/apps", False),
        ("opt", False),
        ("usr/lib/libfoo.so", True),
        ("usr/lib/sub/libfoo.so", False),
        ("etc/ssl/private/key", True),
    ],
)
def test_outputs_select_paths_and_whole_segments(name, selected):
    outputs = ["opt/app", "usr/*/libfoo.so", "./etc/ssl/"]
    assert container_build.selected(name, outputs) == selected


@pytest.mark.parametrize(
    "name,excluded",
    [("out/skip", True), ("out/skip/inner", True), ("out/keep", False), ("a/b/x.pyc", True)],
)
def test_excludes_match_like_tar(name, excluded):
    assert container_build.excluded(name, ["./out/skip", "*.pyc"]) == excluded


def export_tar(path: Path) -> None:
    """An export like podman's, with owners, modes, a hardlink and things that aren't outputs."""
    entries = [
        ("opt", tarfile.DIRTYPE, 0o755, 0, b""),
        ("opt/app", tarfile.DIRTYPE, 0o700, 65532, b""),
        ("opt/app/secret", tarfile.REGTYPE, 0o600, 65532, b"secret"),
        ("opt/app/run", tarfile.REGTYPE, 0o4755, 0, b"#!"),
        ("opt/app/skip.pyc", tarfile.REGTYPE, 0o644, 0, b"pyc"),
        ("opt/other", tarfile.DIRTYPE, 0o755, 0, b""),
        ("opt/other/data", tarfile.REGTYPE, 0o644, 0, b"data"),
        ("opt/app/link", tarfile.LNKTYPE, 0o644, 0, b""),
    ]
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, kind, mode, owner, data in entries:
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.uid, info.gid, info.mtime = kind, mode, owner, owner, 1234567
            info.uname = "someone"
            if kind == tarfile.LNKTYPE:
                info.linkname = "opt/other/data"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("dest", ["dist/out.tar", "out.tar.gz"])
def test_write_outputs_keeps_modes_owners_and_links(tmp_path, dest):
    export_tar(tmp_path / "export.tar")
    out = tmp_path / dest
    container_build.write_outputs(str(tmp_path / "export.tar"), ["opt/app"], ["*.pyc"], str(out))
    with tarfile.open(out) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert list(members) == [
            ".",
            "./opt",
            "./opt/app",
            "./opt/app/link",
            "./opt/app/run",
            "./opt/app/secret",
        ]
        assert {m.mtime for m in members.values()} == {0}
        assert {m.uname for m in members.values()} == {""}
        assert (members["./opt/app"].mode, members["./opt/app"].uid) == (0o700, 65532)
        assert (members["./opt/app/secret"].mode, members["./opt/app/secret"].gid) == (0o600, 65532)
        assert members["./opt/app/run"].mode == 0o4755
        # The link's target isn't an output, so the link becomes the file.
        assert members["./opt/app/link"].isreg()
        assert tar.extractfile("./opt/app/link").read() == b"data"


def test_write_outputs_keeps_links_between_outputs(tmp_path):
    export_tar(tmp_path / "export.tar")
    container_build.write_outputs(str(tmp_path / "export.tar"), ["opt"], [], str(tmp_path / "out.tar"))
    with tarfile.open(tmp_path / "out.tar") as tar:
        link = tar.getmember("./opt/app/link")
        assert link.islnk() and link.linkname == "./opt/other/data"


def test_write_outputs_globs_keep_paths(tmp_path):
    export_tar(tmp_path / "export.tar")
    container_build.write_outputs(str(tmp_path / "export.tar"), ["opt/*/data"], [], str(tmp_path / "out.tar"))
    with tarfile.open(tmp_path / "out.tar") as tar:
        assert tar.getnames() == [".", "./opt", "./opt/other", "./opt/other/data"]


def test_write_outputs_is_deterministic(tmp_path):
    export_tar(tmp_path / "export.tar")
    for name in ("a.tar.gz", "b.tar.gz"):
        container_build.write_outputs(str(tmp_path / "export.tar"), ["opt"], [], str(tmp_path / name))
    assert (tmp_path / "a.tar.gz").read_bytes() == (tmp_path / "b.tar.gz").read_bytes()


def test_write_outputs_rejects_missing_outputs(tmp_path):
    export_tar(tmp_path / "export.tar")
    with pytest.raises(b.LayoutError, match=re.escape("not found in the image: opt/*.so")):
        container_build.write_outputs(
            str(tmp_path / "export.tar"), ["opt", "opt/*.so"], [], str(tmp_path / "o.tar")
        )
