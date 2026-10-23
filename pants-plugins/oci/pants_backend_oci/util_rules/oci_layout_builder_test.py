# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import oci_layout_builder as b

INIT = {"op": "init", "architecture": "amd64", "os": "linux"}
DOCKER_CONFIG = "application/vnd.docker.container.image.v1+json"
DOCKER_LAYER = "application/vnd.docker.image.rootfs.diff.tar.gzip"


def sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def make_tar(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


def write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def image(layout: Path) -> tuple[dict, dict, dict]:
    index = json.loads((layout / "index.json").read_bytes())
    descriptor = b.tagged_descriptor(index, "build")
    manifest = json.loads(b.read_blob(str(layout), descriptor["digest"]))
    config = json.loads(b.read_blob(str(layout), manifest["config"]["digest"]))
    return descriptor, manifest, config


def blobs(layout: Path) -> set[str]:
    return {f"sha256:{name}" for name in os.listdir(layout / "blobs" / "sha256")}


def test_init_creates_an_empty_tagged_image(tmp_path: Path) -> None:
    layout = tmp_path / "build"

    digest = b.apply(str(layout), "build", [INIT])

    assert json.loads((layout / "oci-layout").read_bytes()) == {"imageLayoutVersion": "1.0.0"}
    descriptor, manifest, config = image(layout)
    assert descriptor["digest"] == digest
    assert descriptor["annotations"] == {b.REF_NAME: "build"}
    assert manifest["layers"] == []
    assert manifest["config"]["mediaType"] == b.CONFIG_MEDIA_TYPE
    assert config == {
        "created": b.EPOCH,
        "author": b.AUTHOR,
        "architecture": "amd64",
        "os": "linux",
        "config": {"Env": [b.BUILT_BY]},
        "rootfs": {"type": "layers", "diff_ids": []},
    }
    assert blobs(layout) == {digest, manifest["config"]["digest"]}


@pytest.mark.parametrize("compressed", [False, True])
def test_add_layer_records_digest_diff_id_and_history(tmp_path: Path, compressed: bool) -> None:
    layout = tmp_path / "build"
    tar = make_tar({"hello.txt": b"hello"})
    data = gzip.compress(tar, mtime=1234) if compressed else tar
    path = write(tmp_path / "layers" / "layer.tar", data)
    history = {"created": b.EPOCH, "created_by": "Layer target: //:layer"}

    b.apply(str(layout), "build", [INIT, {"op": "add_layer", "path": path, "history": history}])

    _, manifest, config = image(layout)
    (layer,) = manifest["layers"]
    blob = b.read_blob(str(layout), layer["digest"])
    assert layer == {"mediaType": b.LAYER_MEDIA_TYPE, "digest": sha(blob), "size": len(blob)}
    assert gzip.decompress(blob) == tar
    # A fixed gzip header: no mtime and no file name.
    assert blob[4:8] == b"\0\0\0\0" and not blob[3] & 0x08
    assert config["rootfs"]["diff_ids"] == [sha(tar)]
    assert config["history"] == [history]


def test_add_layer_is_deterministic(tmp_path: Path) -> None:
    tar = make_tar({"a": b"a" * 100_000, "b": os.urandom(1000)})
    layer = write(tmp_path / "layer.tar", tar)
    ops = [INIT, {"op": "add_layer", "path": layer, "history": None}]

    first = b.apply(str(tmp_path / "one"), "build", ops)
    second = b.apply(str(tmp_path / "two"), "build", ops)

    assert first == second
    assert blobs(tmp_path / "one") == blobs(tmp_path / "two")


@pytest.mark.parametrize("compressed", [False, True])
def test_compress_then_add_blob_matches_add_layer(tmp_path: Path, compressed: bool) -> None:
    tar = make_tar({"a": b"a" * 100_000, "b": os.urandom(1000)})
    layer = write(tmp_path / "layer.tar", gzip.compress(tar, mtime=1234) if compressed else tar)
    history = {"created_by": "Layer target: //:layer"}

    assert b.main(["compress", layer, "--out", str(tmp_path / "out")]) == 0
    descriptor = json.loads((tmp_path / "out" / b.LAYER_DESCRIPTOR).read_bytes())
    blob = (tmp_path / "out" / "blobs" / "sha256" / descriptor["digest"][7:]).read_bytes()
    assert descriptor == {
        "mediaType": b.LAYER_MEDIA_TYPE,
        "digest": sha(blob),
        "size": len(blob),
        "diff_id": sha(tar),
    }

    path = str(tmp_path / "out" / b.LAYER_DESCRIPTOR)
    from_blob = b.apply(
        str(tmp_path / "one"), "build", [INIT, {"op": "add_blob", "path": path, "history": history}]
    )
    from_tar = b.apply(
        str(tmp_path / "two"), "build", [INIT, {"op": "add_layer", "path": layer, "history": history}]
    )

    assert from_blob == from_tar
    assert blobs(tmp_path / "one") == blobs(tmp_path / "two")


def test_add_blob_already_in_the_layout(tmp_path: Path) -> None:
    layer = write(tmp_path / "layer.tar", make_tar({"a": b"a"}))
    b.compress_to_blob(layer, str(tmp_path / "first"))
    b.compress_to_blob(layer, str(tmp_path / "second"))
    ops = [
        INIT,
        {"op": "add_blob", "path": str(tmp_path / "first" / b.LAYER_DESCRIPTOR), "history": None},
        {"op": "add_blob", "path": str(tmp_path / "second" / b.LAYER_DESCRIPTOR), "history": None},
    ]

    b.apply(str(tmp_path / "build"), "build", ops)

    _, manifest, config = image(tmp_path / "build")
    assert len(manifest["layers"]) == 2
    assert manifest["layers"][0] == manifest["layers"][1]
    assert config["rootfs"]["diff_ids"] == [sha(Path(layer).read_bytes())] * 2


def test_add_blob_copies_a_symlinked_blob(tmp_path: Path) -> None:
    layer = write(tmp_path / "layer.tar", make_tar({"a": b"a"}))
    digest = b.compress_to_blob(layer, str(tmp_path / "store"))
    # Like a sandbox input linked to a file in the store.
    linked = tmp_path / "sandbox" / "blobs" / "sha256" / digest[7:]
    linked.parent.mkdir(parents=True)
    linked.symlink_to(tmp_path / "store" / "blobs" / "sha256" / digest[7:])
    (tmp_path / "sandbox" / b.LAYER_DESCRIPTOR).write_bytes(
        (tmp_path / "store" / b.LAYER_DESCRIPTOR).read_bytes()
    )

    path = str(tmp_path / "sandbox" / b.LAYER_DESCRIPTOR)
    b.apply(str(tmp_path / "build"), "build", [INIT, {"op": "add_blob", "path": path, "history": None}])

    blob = tmp_path / "build" / "blobs" / "sha256" / digest[7:]
    assert blob.is_file() and not blob.is_symlink()
    assert sha(blob.read_bytes()) == digest


@pytest.mark.parametrize(
    "data, error",
    [
        (b"\x1f\x8b" + b"not gzip at all", "not a valid gzip stream"),
        (b"\x28\xb5\x2f\xfd" + b"\0" * 600, "zstd-compressed"),
        (b"hello, not a tar" * 100, "not a tar archive"),
    ],
)
def test_compress_rejects_bad_input(tmp_path: Path, data: bytes, error: str, capsys) -> None:
    path = write(tmp_path / "layer.tar", data)

    assert b.main(["compress", path, "--out", str(tmp_path / "out")]) == 1

    assert error in capsys.readouterr().err
    assert os.listdir(tmp_path / "out" / "blobs" / "sha256") == []


def test_layers_are_appended_in_order(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    one = write(tmp_path / "one.tar", make_tar({"one": b"1"}))
    two = write(tmp_path / "two.tar", make_tar({"two": b"2"}))
    b.apply(str(layout), "build", [INIT, {"op": "add_layer", "path": one, "history": None}])

    b.apply(str(layout), "build", [{"op": "add_layer", "path": two, "history": None}])

    _, manifest, config = image(layout)
    assert config["rootfs"]["diff_ids"] == [
        sha(Path(one).read_bytes()),
        sha(Path(two).read_bytes()),
    ]
    assert len(manifest["layers"]) == 2
    assert config["history"] == [{"created": b.EPOCH}, {"created": b.EPOCH}]


def configured(tmp_path: Path, *ops: dict) -> dict:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT, *({"op": "config", **op} for op in ops)])
    return image(layout)[2]


def test_config_sets_env_by_key(tmp_path: Path) -> None:
    config = configured(tmp_path, {"env": ["A=1", "B=2"]}, {"env": ["A=3", b.BUILT_BY, "C="]})

    assert config["config"]["Env"] == [b.BUILT_BY, "A=3", "B=2", "C="]


def test_config_replaces_entrypoint_cmd_and_user(tmp_path: Path) -> None:
    config = configured(
        tmp_path,
        {"entrypoint": ["/bin/sh"], "cmd": ["-c", "true"], "user": "nonroot"},
        {"cmd": ["--version"]},
    )

    assert config["config"]["Entrypoint"] == ["/bin/sh"]
    assert config["config"]["Cmd"] == ["--version"]
    assert config["config"]["User"] == "nonroot"


def test_config_clears_before_setting(tmp_path: Path) -> None:
    config = configured(
        tmp_path,
        {"entrypoint": ["/old"], "cmd": ["old"]},
        {"clear": ["config.entrypoint", "config.cmd"], "entrypoint": ["/new"]},
    )

    assert config["config"]["Entrypoint"] == ["/new"]
    assert "Cmd" not in config["config"]


def test_config_rejects_unknown_clear(tmp_path: Path) -> None:
    with pytest.raises(b.LayoutError, match="config.workdir"):
        configured(tmp_path, {"clear": ["config.workdir"]})


def test_config_history_is_an_empty_layer(tmp_path: Path) -> None:
    history = {"created": b.EPOCH, "created_by": "pants_backend_oci config"}

    config = configured(tmp_path, {"user": "1"}, {"user": "2", "history": history})

    assert config["history"] == [{**history, "empty_layer": True}]


def test_config_sets_author_and_created(tmp_path: Path) -> None:
    config = configured(tmp_path, {"author": "someone", "created": "2000-01-01T00:00:00Z"})

    assert config["author"] == "someone"
    assert config["created"] == "2000-01-01T00:00:00Z"


def test_unknown_op_fails(tmp_path: Path) -> None:
    with pytest.raises(b.LayoutError, match="unknown op 'squash'"):
        b.apply(str(tmp_path / "build"), "build", [INIT, {"op": "squash"}])


def test_retagging_keeps_other_manifests_and_drops_unused_blobs(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])
    other = b.apply(str(layout), "other", [INIT, {"op": "config", "user": "other"}])
    first = b.image_digest(str(layout), "build")

    second = b.apply(str(layout), "build", [{"op": "config", "user": "1"}])

    index = json.loads((layout / "index.json").read_bytes())
    assert [m["digest"] for m in index["manifests"]] == [other, second]
    assert first not in blobs(layout)
    assert {other, second} <= blobs(layout)


def test_apply_without_ops_reports_the_digest(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    digest = b.apply(str(layout), "build", [INIT])

    assert b.apply(str(layout), "build", []) == digest


def test_missing_tag_fails(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])

    with pytest.raises(b.LayoutError, match="found 0"):
        b.apply(str(layout), "missing", [{"op": "config", "user": "1"}])


def pulled_layout(layout: Path, manifest: dict, descriptor: dict | None = None) -> bytes:
    content = json.dumps(manifest).encode()
    write(layout / "oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
    write(layout / "blobs" / "sha256" / sha(content)[7:], content)
    for desc in [manifest["config"], *manifest["layers"]]:
        write(layout / "blobs" / "sha256" / desc["digest"][7:], b"blob")
    entry = {
        "mediaType": manifest["mediaType"],
        "digest": sha(content),
        "size": len(content),
        **(descriptor or {}),
    }
    write(layout / "index.json", json.dumps({"schemaVersion": 2, "manifests": [entry]}).encode())
    return content


def test_normalize_converts_docker_manifest_and_tags_it(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    docker = pulled_layout(
        layout,
        {
            "schemaVersion": 2,
            "mediaType": b.DOCKER_MANIFEST,
            "config": {"mediaType": DOCKER_CONFIG, "digest": f"sha256:{'c' * 64}"},
            "layers": [{"mediaType": DOCKER_LAYER, "digest": f"sha256:{'1' * 64}"}],
        },
        {"artifactType": DOCKER_CONFIG, "annotations": {"keep": "me"}},
    )

    digest = b.normalize(str(layout), "build")

    index = json.loads((layout / "index.json").read_bytes())
    (descriptor,) = index["manifests"]
    manifest = b.read_blob(str(layout), digest)
    assert descriptor == {
        "mediaType": b.MANIFEST_MEDIA_TYPE,
        "digest": sha(manifest),
        "size": len(manifest),
        "artifactType": b.CONFIG_MEDIA_TYPE,
        "annotations": {"keep": "me", b.REF_NAME: "build"},
    }
    assert json.loads(manifest)["layers"][0]["mediaType"] == b.LAYER_MEDIA_TYPE
    assert sha(docker) not in blobs(layout)
    assert {f"sha256:{'c' * 64}", f"sha256:{'1' * 64}"} <= blobs(layout)


def test_normalize_keeps_an_oci_manifest_byte_identical(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    content = pulled_layout(
        layout,
        {
            "schemaVersion": 2,
            "mediaType": b.MANIFEST_MEDIA_TYPE,
            "config": {"mediaType": b.CONFIG_MEDIA_TYPE, "digest": f"sha256:{'c' * 64}"},
            "layers": [],
        },
    )

    assert b.normalize(str(layout), "build") == sha(content)


def test_docker_manifest_to_oci_keeps_oci_media_types() -> None:
    manifest = {
        "mediaType": b.DOCKER_MANIFEST,
        "config": {"mediaType": DOCKER_CONFIG, "digest": "sha256:c"},
        "layers": [
            {"mediaType": DOCKER_LAYER, "digest": "sha256:l"},
            {"mediaType": "application/vnd.oci.image.layer.v1.tar+zstd", "digest": "sha256:z"},
        ],
    }

    converted = json.loads(b.docker_manifest_to_oci(json.dumps(manifest).encode()))

    assert converted["mediaType"] == b.MANIFEST_MEDIA_TYPE
    assert converted["config"] == {"mediaType": b.CONFIG_MEDIA_TYPE, "digest": "sha256:c"}
    assert [layer["mediaType"] for layer in converted["layers"]] == [
        b.LAYER_MEDIA_TYPE,
        "application/vnd.oci.image.layer.v1.tar+zstd",
    ]


def test_docker_manifest_to_oci_rejects_unknown_media_types() -> None:
    manifest = {
        "mediaType": b.DOCKER_MANIFEST,
        "config": {"mediaType": "application/vnd.docker.plugin.v1+json"},
        "layers": [],
    }

    with pytest.raises(b.LayoutError, match="plugin"):
        b.docker_manifest_to_oci(json.dumps(manifest).encode())


@pytest.mark.parametrize("count", [0, 2])
def test_normalize_requires_one_manifest(tmp_path: Path, count: int) -> None:
    layout = tmp_path / "build"
    manifests = [{"mediaType": b.MANIFEST_MEDIA_TYPE, "digest": f"sha256:{i}"} for i in range(count)]
    write(layout / "index.json", json.dumps({"schemaVersion": 2, "manifests": manifests}).encode())

    with pytest.raises(b.LayoutError, match=f"found {count}"):
        b.normalize(str(layout), "build")


def test_main_applies_a_spec_and_prints_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "spec.json", json.dumps([INIT, {"op": "config", "cmd": ["a"]}]).encode())

    assert b.main(["apply", "spec.json"]) == 0
    digest = capsys.readouterr().out.strip()
    assert digest == b.image_digest("build", "build")

    assert b.main(["set-cmd", "--", "--version", "-x"]) == 0
    assert image(tmp_path / "build")[2]["config"]["Cmd"] == ["--version", "-x"]

    # Without args the cmd is left alone.
    assert b.main(["set-cmd", "--"]) == 0
    assert image(tmp_path / "build")[2]["config"]["Cmd"] == ["--version", "-x"]


def test_main_reports_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert b.main(["digest"]) == 1
    assert capsys.readouterr().err.startswith("error: build is not an OCI layout")


@pytest.mark.parametrize(
    "data, error",
    [
        (gzip.compress(make_tar({"a": b"a" * 10_000}))[:-20], "not a valid gzip stream"),
        (b"\x1f\x8b" + b"not gzip at all", "not a valid gzip stream"),
        (b"\x28\xb5\x2f\xfd" + b"\0" * 600, "zstd-compressed"),
        (b"hello, not a tar" * 100, "not a tar archive"),
        (b"", "not a tar archive"),
    ],
)
def test_add_layer_rejects_bad_input(tmp_path: Path, data: bytes, error: str) -> None:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])
    before = blobs(layout)
    path = write(tmp_path / "layer.tar", data)

    with pytest.raises(b.LayoutError, match=error):
        b.apply(str(layout), "build", [{"op": "add_layer", "path": path, "history": None}])

    # Nothing is left behind, not even a temporary blob.
    assert sorted(os.listdir(layout / "blobs" / "sha256")) == sorted(d[7:] for d in before)


def test_add_layer_accepts_an_empty_tar(tmp_path: Path) -> None:
    path = write(tmp_path / "layer.tar", make_tar({}))

    b.apply(str(tmp_path / "build"), "build", [INIT, {"op": "add_layer", "path": path, "history": None}])

    assert image(tmp_path / "build")[2]["rootfs"]["diff_ids"] == [sha(make_tar({}))]


def test_add_layer_pads_history_of_a_base_without_it(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])
    # A base with two layers but history for only one, followed by an empty-layer entry.
    base = b.load_image(str(layout), "build")
    base.config["rootfs"]["diff_ids"] = [sha(b"1"), sha(b"2")]
    base.config["history"] = [{"created_by": "one"}, {"created_by": "cmd", "empty_layer": True}]
    b.save_image(str(layout), "build", base)
    path = write(tmp_path / "layer.tar", make_tar({"a": b"a"}))

    b.apply(str(layout), "build", [{"op": "add_layer", "path": path, "history": {"created_by": "new"}}])

    history = image(layout)[2]["history"]
    assert [entry.get("created_by") for entry in history] == ["one", "cmd", None, "new"]
    assert len([entry for entry in history if not entry.get("empty_layer")]) == 3


def test_garbage_collection_skips_layouts_with_unknown_manifests(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])
    artifact = write(layout / "blobs" / "sha256" / ("a" * 64), b"{}")
    referenced_by_artifact = write(layout / "blobs" / "sha256" / ("b" * 64), b"blob")
    index = json.loads((layout / "index.json").read_bytes())
    index["manifests"].append(
        {"mediaType": "application/vnd.example.artifact+json", "digest": f"sha256:{'a' * 64}"}
    )
    b.write_index(str(layout), index)

    b.apply(str(layout), "build", [{"op": "config", "user": "1"}])

    assert os.path.exists(artifact) and os.path.exists(referenced_by_artifact)


def test_retagging_drops_embedded_data_and_urls(tmp_path: Path) -> None:
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])
    index = json.loads((layout / "index.json").read_bytes())
    index["manifests"][0].update(data="e30=", urls=["https://example.com"], platform={"os": "linux"})
    b.write_index(str(layout), index)

    b.apply(str(layout), "build", [{"op": "config", "user": "1"}])

    descriptor = image(layout)[0]
    assert "data" not in descriptor and "urls" not in descriptor
    assert descriptor["platform"] == {"os": "linux"}


@pytest.mark.parametrize("digest", ["sha256:..", "sha256:abc", "sha512:" + "0" * 128, "sha256:" + "A" * 64])
def test_blob_path_rejects_unsupported_digests(digest: str) -> None:
    with pytest.raises(b.LayoutError, match="unsupported blob digest"):
        b.blob_path("build", digest)


def test_set_cmd_keeps_literal_double_dashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    b.apply("build", "build", [INIT])

    assert b.main(["--tag", "build", "set-cmd", "--", "--", "set-cmd", "-x"]) == 0

    assert image(tmp_path / "build")[2]["config"]["Cmd"] == ["--", "set-cmd", "-x"]


def archived_image(tmp_path: Path) -> Path:
    """A layout with one layer, an older retagged manifest and a stray blob."""
    layout = tmp_path / "build"
    b.apply(str(layout), "build", [INIT])
    b.add_layer(
        str(layout),
        b.load_image(str(layout), "build"),
        write(tmp_path / "l.tar", make_tar({"f": b"x"})),
        None,
    )
    b.apply(str(layout), "old", [INIT, {"op": "config", "cmd": ["old"]}])
    write(layout / "blobs" / "sha256" / ("0" * 64), b"stray")
    return layout


def test_archive_holds_only_the_image_and_names_it(tmp_path: Path) -> None:
    layout = archived_image(tmp_path)
    descriptor, manifest, _ = image(layout)
    archive = tmp_path / "out" / "image.tar"

    digest = b.write_archive(str(layout), "build", str(archive), "example.com/app:1.0")

    assert digest == descriptor["digest"]
    own = [
        descriptor["digest"],
        manifest["config"]["digest"],
        *(layer["digest"] for layer in manifest["layers"]),
    ]
    with tarfile.open(archive) as tar:
        assert tar.getnames() == [
            "blobs",
            "blobs/sha256",
            *sorted(f"blobs/sha256/{d[7:]}" for d in own),
            "index.json",
            "manifest.json",
            "oci-layout",
        ]
        assert {(m.mtime, m.uid, m.gid, m.uname, m.gname) for m in tar.getmembers()} == {(0, 0, 0, "", "")}
        index = json.load(tar.extractfile("index.json"))
        docker = json.load(tar.extractfile("manifest.json"))
        assert tar.extractfile(f"blobs/sha256/{digest[7:]}").read() == b.read_blob(str(layout), digest)
    (entry,) = index["manifests"]
    assert entry["digest"] == digest
    assert entry["annotations"] == {
        b.REF_NAME: "example.com/app:1.0",
        "io.containerd.image.name": "example.com/app:1.0",
    }
    assert docker == [
        {
            "Config": f"blobs/sha256/{manifest['config']['digest'][7:]}",
            "RepoTags": ["example.com/app:1.0"],
            "Layers": [f"blobs/sha256/{layer['digest'][7:]}" for layer in manifest["layers"]],
        }
    ]


def test_archive_is_deterministic(tmp_path: Path) -> None:
    layout = archived_image(tmp_path)
    b.write_archive(str(layout), "build", str(tmp_path / "a.tar"), "app:1")
    b.write_archive(str(layout), "build", str(tmp_path / "b.tar"), "app:1")
    assert (tmp_path / "a.tar").read_bytes() == (tmp_path / "b.tar").read_bytes()


def test_archive_without_a_name_is_untagged(tmp_path: Path) -> None:
    layout = archived_image(tmp_path)
    b.write_archive(str(layout), "build", str(tmp_path / "image.tar"), None)
    with tarfile.open(tmp_path / "image.tar") as tar:
        (entry,) = json.load(tar.extractfile("index.json"))["manifests"]
        (docker,) = json.load(tar.extractfile("manifest.json"))
    assert "annotations" not in entry
    assert docker["RepoTags"] is None


def test_main_exports_an_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    archived_image(tmp_path)

    assert b.main(["export", "dist/image.tar", "--name", "app:1"]) == 0
    assert capsys.readouterr().out.strip() == b.image_digest("build", "build")
    with tarfile.open(tmp_path / "dist" / "image.tar") as tar:
        (entry,) = json.load(tar.extractfile("index.json"))["manifests"]
    assert entry["annotations"][b.REF_NAME] == "app:1"
