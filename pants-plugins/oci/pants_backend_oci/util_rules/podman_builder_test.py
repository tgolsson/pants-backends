# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import oci_layout_builder as b
from pants_backend_oci.util_rules import podman_builder, podman_runner

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
with open({log!r} + ".home", "a") as f:
    f.write(os.environ.get("HOME", "") + "\\n")
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
    monkeypatch.delenv("FAKE_BUILD_STATUS", raising=False)
    (tmp_path / "xdg").mkdir()
    monkeypatch.setattr(podman_runner, "runtime_dir", lambda uid: tmp_path / "xdg")
    # As in a builder process, which runs without a HOME.
    monkeypatch.delenv("HOME", raising=False)
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


def test_podman_finds_the_users_home_itself(sandbox, tmp_path):
    # Podman looks the home up when HOME is unset, so it stays out of the cache key.
    _, common, _ = sandbox
    assert podman_builder.main([*common, "--dest", "out.tar", "files", "--output", "out"]) == 0
    homes = set((tmp_path / "podman.log.home").read_text().splitlines())
    assert homes == {""}


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
