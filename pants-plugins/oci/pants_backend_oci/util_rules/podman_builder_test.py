# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import oci_layout_builder as b
from pants_backend_oci.util_rules import podman_builder

INIT = {"op": "init", "architecture": "amd64", "os": "linux"}
RUN = ["/bin/sh", "-c", "echo hi > /out/hi"]
# The package root, so the fake podman can build layouts with the real layout builder.
PACKAGE_ROOT = Path(b.__file__).parents[2]


def make_tar(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


def make_layout(layout: Path, layers: int) -> None:
    ops = [INIT]
    for i in range(layers):
        path = layout.parent / f"layer{i}.tar"
        path.write_bytes(make_tar({f"file{i}": b"base"}))
        ops.append({"op": "add_layer", "path": str(path)})
    b.apply(str(layout), "build", ops)


def test_containerfile_runs_as_root_then_copies_what_outputs_need():
    outputs = ["bin/app", "./etc/x/", "lib/*.so", "usr/*/libfoo.so", "usr/share/doc"]
    assert podman_builder.containerfile("/s/image.tar", "ref", RUN, outputs) == (
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
    assert podman_builder.containerfile("/s/image.tar", "ref", RUN, []) == (
        'FROM oci-archive:/s/image.tar:ref AS build\nUSER 0:0\nRUN ["/bin/sh", "-c", "echo hi > /out/hi"]\n'
    )


def test_containerfile_for_extract_runs_nothing():
    containerfile = podman_builder.containerfile("/s/image.tar", "ref", None, ["bin"])
    assert "RUN" not in containerfile and "USER" not in containerfile
    assert 'COPY --from=build ["/bin", "/bin"]' in containerfile


@pytest.mark.parametrize("dest", [None, "/sandbox/export.tar"])
def test_build_argv(dest):
    argv = podman_builder.build_argv("/usr/bin/podman", ["--root", "/store"], "ctx", "/tmp/iid", dest)
    assert argv[:4] == ["/usr/bin/podman", "--root", "/store", "build"]
    assert {"--layers=false", "--no-hosts", "--timestamp=0", "--iidfile=/tmp/iid"} <= set(argv)
    assert ("--output=type=tar,dest=/sandbox/export.tar" in argv) == (dest is not None)
    assert argv[-3:] == ["--file", "ctx/Containerfile", "ctx"]


def test_archive_names_the_image_by_its_digest(tmp_path):
    make_layout(tmp_path / "build", 1)
    digest = b.image_digest(str(tmp_path / "build"), "build")
    ref = podman_builder.write_archive(str(tmp_path / "build"), "build", str(tmp_path / "image.tar"))
    assert ref == "pants-" + digest.removeprefix("sha256:")
    with tarfile.open(tmp_path / "image.tar") as tar:
        index = json.load(tar.extractfile("index.json"))
        assert {"oci-layout", "blobs"} <= set(tar.getnames())
    (manifest,) = index["manifests"]
    assert manifest["digest"] == digest
    assert manifest["annotations"][b.REF_NAME] == ref


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
    assert podman_builder.selected(name, outputs) == selected


@pytest.mark.parametrize(
    "name,excluded",
    [("out/skip", True), ("out/skip/inner", True), ("out/keep", False), ("a/b/x.pyc", True)],
)
def test_excludes_match_like_tar(name, excluded):
    assert podman_builder.excluded(name, ["./out/skip", "*.pyc"]) == excluded


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
    podman_builder.write_outputs(str(tmp_path / "export.tar"), ["opt/app"], ["*.pyc"], str(out))
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
    podman_builder.write_outputs(str(tmp_path / "export.tar"), ["opt"], [], str(tmp_path / "out.tar"))
    with tarfile.open(tmp_path / "out.tar") as tar:
        link = tar.getmember("./opt/app/link")
        assert link.islnk() and link.linkname == "./opt/other/data"


def test_write_outputs_globs_keep_paths(tmp_path):
    export_tar(tmp_path / "export.tar")
    podman_builder.write_outputs(str(tmp_path / "export.tar"), ["opt/*/data"], [], str(tmp_path / "out.tar"))
    with tarfile.open(tmp_path / "out.tar") as tar:
        assert tar.getnames() == [".", "./opt", "./opt/other", "./opt/other/data"]


def test_write_outputs_is_deterministic(tmp_path):
    export_tar(tmp_path / "export.tar")
    for name in ("a.tar.gz", "b.tar.gz"):
        podman_builder.write_outputs(str(tmp_path / "export.tar"), ["opt"], [], str(tmp_path / name))
    assert (tmp_path / "a.tar.gz").read_bytes() == (tmp_path / "b.tar.gz").read_bytes()


def test_write_outputs_rejects_missing_outputs(tmp_path):
    export_tar(tmp_path / "export.tar")
    with pytest.raises(b.LayoutError, match=re.escape("not found in the image: opt/*.so")):
        podman_builder.write_outputs(
            str(tmp_path / "export.tar"), ["opt", "opt/*.so"], [], str(tmp_path / "o.tar")
        )


def test_top_layer_is_the_one_added_layer(tmp_path):
    saved = tmp_path / "saved"
    make_layout(saved, 3)
    manifest = json.loads(b.read_blob(str(saved), b.image_digest(str(saved), "build")))
    assert podman_builder.top_layer(str(saved), 2) == b.blob_path(
        str(saved), manifest["layers"][-1]["digest"]
    )
    with pytest.raises(b.LayoutError, match="add one layer to the base's 3, found 3"):
        podman_builder.top_layer(str(saved), 3)


FAKE_PODMAN = """\
import io, json, os, shutil, sys, tarfile
sys.path.insert(0, {root!r})
from pants_backend_oci.util_rules import oci_layout_builder as b

args = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(args) + "\\n")
if "build" in args:
    for arg in args:
        if arg.startswith("--iidfile="):
            open(arg.split("=", 1)[1], "w").write("built-image\\n")
        if arg.startswith("--output="):
            with tarfile.open(arg.rsplit("dest=", 1)[1], "w") as tar:
                info = tarfile.TarInfo("out/hi")
                info.size = 2
                tar.addfile(info, io.BytesIO(b"hi"))
    sys.exit(int(os.environ.get("FAKE_BUILD_STATUS", "0")))
if "save" in args:
    saved = next(a for a in args if a.startswith("--output=")).split("=", 1)[1]
    shutil.copytree("build", saved)
    open("added.tar", "wb").write(open("layer-to-add.tar", "rb").read())
    b.apply(saved, "build", [{{"op": "add_layer", "path": "added.tar"}}])
"""


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    if sys.platform != "linux" or os.geteuid() == 0:
        pytest.skip("requires an unprivileged Linux user")
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_CONNECTION", raising=False)
    monkeypatch.delenv("FAKE_BUILD_STATUS", raising=False)
    work = tmp_path / "sandbox"
    work.mkdir()
    monkeypatch.chdir(work)
    make_layout(work / "build", 1)
    log = tmp_path / "podman.log"
    podman = tmp_path / "podman"
    podman.write_text(f"#!{sys.executable}\n" + FAKE_PODMAN.format(root=str(PACKAGE_ROOT), log=str(log)))
    podman.chmod(0o755)
    calls = lambda: [json.loads(line) for line in log.read_text().splitlines()]  # noqa: E731
    return work, ["--podman", str(podman), "--cache", str(tmp_path / "cache")], calls


def test_files_writes_outputs_and_removes_the_image(sandbox):
    work, common, calls = sandbox
    status = podman_builder.main(
        [*common, "--run", json.dumps(RUN), "--dest", "out.tar", "files", "--output", "out"]
    )
    assert status == 0
    with tarfile.open(work / "out.tar") as tar:
        assert tar.getnames() == [".", "./out", "./out/hi"]
    build, rmi = calls()
    assert build[build.index("--root") + 1] == str((Path(common[3]) / "storage").resolve())
    assert rmi[-3:] == ["rmi", "--force", "built-image"]
    containerfile = (work / "podman-context" / "Containerfile").read_text()
    digest = b.image_digest(str(work / "build"), "build").removeprefix("sha256:")
    assert containerfile.startswith(f"FROM oci-archive:{work / 'image.tar'}:pants-{digest} AS build\n")


def test_missing_output_fails(sandbox, capsys):
    _, common, calls = sandbox
    status = podman_builder.main([*common, "--dest", "out.tar", "files", "--output", "nothing"])
    assert status == 1
    assert "outputs not found in the image: nothing" in capsys.readouterr().err
    assert calls()[-1][-3:] == ["rmi", "--force", "built-image"]


def test_failed_build_fails_and_still_removes_the_image(sandbox, monkeypatch):
    _, common, calls = sandbox
    monkeypatch.setenv("FAKE_BUILD_STATUS", "3")
    status = podman_builder.main(
        [*common, "--run", json.dumps(RUN), "--dest", "out.tar", "files", "--output", "out"]
    )
    assert status == 3
    assert [call[-3:] for call in calls()][-1] == ["rmi", "--force", "built-image"]


def test_layer_writes_the_added_layer(sandbox):
    work, common, calls = sandbox
    added = make_tar({"out/hi": b"hi"})
    (work / "layer-to-add.tar").write_bytes(added)
    status = podman_builder.main([*common, "--run", json.dumps(RUN), "--dest", "scripted-layer.tar", "layer"])
    assert status == 0
    with tarfile.open(work / "scripted-layer.tar") as tar:
        assert tar.getnames() == ["out/hi"]
    assert [call[call.index("--storage-driver=vfs") + 1] for call in calls()] == ["build", "save", "rmi"]


def test_layer_needs_a_command(sandbox):
    _, common, _ = sandbox
    with pytest.raises(SystemExit):
        podman_builder.main([*common, "--dest", "scripted-layer.tar", "layer"])
