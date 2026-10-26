# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import gzip
import io
import json
import os
import pwd
import sys
import tarfile
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import docker_builder
from pants_backend_oci.util_rules import oci_layout_builder as b

INIT = {"op": "init", "architecture": "amd64", "os": "linux"}
RUN = ["/bin/sh", "-c", "echo hi > /out/hi"]


def layer_tar() -> bytes:
    """A layer as Docker saves it: real times, owner names, a capability and a whiteout."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        out_dir = tarfile.TarInfo("out")
        out_dir.type, out_dir.mode, out_dir.mtime = tarfile.DIRTYPE, 0o755, 1700000000
        tar.addfile(out_dir)
        info = tarfile.TarInfo("out/hi")
        info.size, info.mtime, info.uid, info.uname = 2, 1700000000, 65532, "nonroot"
        info.pax_headers = {"SCHILY.xattr.security.capability": "cap", "atime": "1700000001.5"}
        tar.addfile(info, io.BytesIO(b"hi"))
        whiteout = tarfile.TarInfo("etc/.wh.gone")
        whiteout.mtime = 1700000000
        tar.addfile(whiteout, io.BytesIO(b""))
    return out.getvalue()


def make_layout(layout: Path, layers: int) -> None:
    ops = [INIT]
    for i in range(layers):
        path = layout.parent / f"layer{i}.tar"
        with tarfile.open(path, "w") as tar:
            info = tarfile.TarInfo(f"file{i}")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"base"))
        ops.append({"op": "add_layer", "path": str(path)})
    b.apply(str(layout), "build", ops)


def saved_tar(path: Path, layers: list[bytes], oci: bool) -> None:
    """A `docker save` tar: Docker's own layout before 25, an OCI one after, both with
    manifest.json."""
    names = [f"blobs/sha256/{i:064x}" if oci else f"{i:064x}/layer.tar" for i in range(len(layers))]
    files = {name: data for name, data in zip(names, layers)}
    files["manifest.json"] = json.dumps(
        [{"Config": "config.json", "RepoTags": None, "Layers": names}]
    ).encode()
    with tarfile.open(path, "w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_image_name_is_the_digest_with_a_tag(tmp_path):
    make_layout(tmp_path / "build", 1)
    digest = b.image_digest(str(tmp_path / "build"), "build")
    assert docker_builder.image_name(str(tmp_path / "build"), "build") == (
        "pants-" + digest.removeprefix("sha256:") + ":latest"
    )


@pytest.mark.parametrize("dest", [None, "/sandbox/export.tar"])
def test_build_argv(dest):
    argv = docker_builder.build_argv("/usr/bin/docker", "ctx", "pants-step-x:latest", dest)
    assert argv[:2] == ["/usr/bin/docker", "build"]
    # Pants decides when a step reruns, not Docker's own build cache.
    assert "--no-cache" in argv
    assert ("--tag=pants-step-x:latest" in argv) == (dest is None)
    assert ("--output=type=tar,dest=/sandbox/export.tar" in argv) == (dest is not None)
    assert argv[-3:] == ["--file", "ctx/Containerfile", "ctx"]


@pytest.mark.parametrize("compress", [False, True])
def test_layer_loses_times_and_owner_names_only(tmp_path, compress):
    data = layer_tar()
    docker_builder.write_layer(io.BytesIO(gzip.compress(data) if compress else data), str(tmp_path / "l.tar"))
    with tarfile.open(tmp_path / "l.tar") as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == ["out", "out/hi", "etc/.wh.gone"]
        assert {m.mtime for m in members} == {0}
        hi = tar.getmember("out/hi")
        assert (hi.uid, hi.uname) == (65532, "")
        assert hi.pax_headers.get("SCHILY.xattr.security.capability") == "cap"
        assert "atime" not in hi.pax_headers
        assert tar.extractfile(hi).read() == b"hi"


def test_layer_is_deterministic(tmp_path):
    for name in ("a.tar", "b.tar"):
        docker_builder.write_layer(io.BytesIO(layer_tar()), str(tmp_path / name))
    assert (tmp_path / "a.tar").read_bytes() == (tmp_path / "b.tar").read_bytes()


def built_tag(build: list[str]) -> str:
    return next(arg for arg in build if arg.startswith("--tag=")).split("=", 1)[1]


@pytest.mark.parametrize("oci", [False, True])
def test_top_layer_is_the_one_added_layer(tmp_path, oci):
    saved_tar(tmp_path / "saved.tar", [layer_tar(), gzip.compress(layer_tar())], oci)
    docker_builder.write_top_layer(str(tmp_path / "saved.tar"), 1, str(tmp_path / "top.tar"))
    with tarfile.open(tmp_path / "top.tar") as tar:
        assert tar.getnames() == ["out", "out/hi", "etc/.wh.gone"]


@pytest.mark.parametrize("base_layers", [0, 3])
def test_top_layer_rejects_unexpected_layer_counts(tmp_path, base_layers):
    # Two layers: too many for an empty base, too few for a base of three.
    saved_tar(tmp_path / "saved.tar", [layer_tar(), layer_tar()], oci=True)
    with pytest.raises(b.LayoutError, match=f"add one layer to the base's {base_layers}, found 2"):
        docker_builder.write_top_layer(str(tmp_path / "saved.tar"), base_layers, str(tmp_path / "top.tar"))


FAKE_DOCKER = """\
import io, json, os, shutil, sys, tarfile

args = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(args) + "\\n")
with open({log!r} + ".home", "a") as f:
    f.write(os.environ["HOME"] + "\\n")
if args[0] == "load":
    with tarfile.open(args[args.index("--input") + 1]) as tar:
        print("Loaded image:", json.load(tar.extractfile("manifest.json"))[0]["RepoTags"][0])
    sys.exit(int(os.environ.get("FAKE_LOAD_STATUS", "0")))
if args[0] == "build":
    for arg in args:
        if arg.startswith("--output="):
            with tarfile.open(arg.rsplit("dest=", 1)[1], "w") as tar:
                info = tarfile.TarInfo("out/hi")
                info.size = 2
                tar.addfile(info, io.BytesIO(b"hi"))
    print("sha256:built")
    sys.exit(int(os.environ.get("FAKE_BUILD_STATUS", "0")))
if args[0] == "save":
    shutil.copyfile("saved-by-docker.tar", args[args.index("--output") + 1])
"""


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for var in ("FAKE_LOAD_STATUS", "FAKE_BUILD_STATUS"):
        monkeypatch.delenv(var, raising=False)
    # As in a builder process, which runs without a HOME.
    monkeypatch.delenv("HOME", raising=False)
    work = tmp_path / "sandbox"
    work.mkdir()
    monkeypatch.chdir(work)
    make_layout(work / "build", 1)
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER.format(log=str(log)))
    docker.chmod(0o755)
    calls = lambda: [json.loads(line) for line in log.read_text().splitlines()]  # noqa: E731
    return work, ["--docker", str(docker)], calls


def test_files_loads_the_base_by_name_and_writes_outputs(sandbox, capfd):
    work, common, calls = sandbox
    status = docker_builder.main(
        [*common, "--run", json.dumps(RUN), "--dest", "out.tar", "files", "--output", "out"]
    )
    assert status == 0
    with tarfile.open(work / "out.tar") as tar:
        assert tar.getnames() == [".", "./out", "./out/hi"]
    name = docker_builder.image_name(str(work / "build"), "build")
    # Exporting files builds no image, so there is nothing to remove.
    load, build = calls()
    assert load == ["load", "--quiet", "--input", str(work / "image.tar")]
    assert build[0] == "build" and not any(arg.startswith("--tag") for arg in build)
    with tarfile.open(work / "image.tar") as tar:
        assert json.load(tar.extractfile("manifest.json"))[0]["RepoTags"] == [name]
    containerfile = (work / "docker-context" / "Containerfile").read_text()
    assert containerfile.startswith(f"FROM {name} AS build\n")
    # Neither the load nor the build's image id is the step's output.
    assert capfd.readouterr().out == ""


def test_docker_gets_the_users_home(sandbox, tmp_path):
    _, common, _ = sandbox
    assert docker_builder.main([*common, "--dest", "out.tar", "files", "--output", "out"]) == 0
    homes = set((tmp_path / "docker.log.home").read_text().splitlines())
    assert homes == {pwd.getpwuid(os.getuid()).pw_dir}


def test_failed_load_fails_with_its_output(sandbox, monkeypatch, capfd):
    _, common, calls = sandbox
    monkeypatch.setenv("FAKE_LOAD_STATUS", "4")
    assert docker_builder.main([*common, "--dest", "out.tar", "files", "--output", "out"]) == 4
    assert "Loaded image" in capfd.readouterr().err
    assert [call[0] for call in calls()] == ["load"]


def test_failed_build_fails_and_still_removes_the_image(sandbox, monkeypatch):
    _, common, calls = sandbox
    monkeypatch.setenv("FAKE_BUILD_STATUS", "3")
    status = docker_builder.main([*common, "--run", json.dumps(RUN), "--dest", "layer.tar", "layer"])
    assert status == 3
    build, rmi = calls()[1:]
    assert rmi == ["rmi", built_tag(build)]


def test_layer_writes_the_added_layer(sandbox):
    work, common, calls = sandbox
    saved_tar(work / "saved-by-docker.tar", [b"base layer is not read", layer_tar()], oci=True)
    status = docker_builder.main([*common, "--run", json.dumps(RUN), "--dest", "scripted-layer.tar", "layer"])
    assert status == 0
    with tarfile.open(work / "scripted-layer.tar") as tar:
        assert tar.getnames() == ["out", "out/hi", "etc/.wh.gone"]
        assert {m.mtime for m in tar.getmembers()} == {0}
    _, build, save, rmi = calls()
    # Only this build's own tag is saved and removed, never the image by id.
    tag = built_tag(build)
    assert tag.startswith("pants-step-")
    assert save[-1] == tag and rmi == ["rmi", tag]


def test_a_step_that_changes_nothing_adds_an_empty_layer(sandbox):
    work, common, _ = sandbox
    # BuildKit records such a step in the history only, so the save has just the base's layer.
    saved_tar(work / "saved-by-docker.tar", [b"base layer is not read"], oci=True)
    status = docker_builder.main([*common, "--run", json.dumps(RUN), "--dest", "scripted-layer.tar", "layer"])
    assert status == 0
    with tarfile.open(work / "scripted-layer.tar") as tar:
        assert tar.getnames() == []
    # It's a layer the image can still be assembled with.
    assert b.compress_to_blob(str(work / "scripted-layer.tar"), str(work / "blob")).startswith("sha256:")


def test_docker_gets_a_configured_home(sandbox, tmp_path, monkeypatch):
    _, common, _ = sandbox
    monkeypatch.setenv(docker_builder.DOCKER_HOME, "/configured/home")
    assert docker_builder.main([*common, "--dest", "out.tar", "files", "--output", "out"]) == 0
    homes = set((tmp_path / "docker.log.home").read_text().splitlines())
    assert homes == {"/configured/home"}


def test_missing_docker(sandbox, tmp_path, capsys):
    status = docker_builder.main(
        ["--docker", str(tmp_path / "nope"), "--dest", "o.tar", "files", "--output", "out"]
    )
    assert status == 127
    assert "docker is missing" in capsys.readouterr().err


def test_layer_needs_a_command(sandbox):
    _, common, _ = sandbox
    with pytest.raises(SystemExit):
        docker_builder.main([*common, "--dest", "scripted-layer.tar", "layer"])
