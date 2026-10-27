# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from pants.core.util_rules import adhoc_binaries, external_tool
from pants.engine import process
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST, Digest, DigestContents, Snapshot
from pants.engine.internals.scheduler import ExecutionError
from pants.testutil.rule_runner import QueryRule, RuleRunner

from pants_backend_oci.util_rules import assemble, oci_layout_builder, sandboxed
from pants_backend_oci.util_rules.assemble import (
    AssembleImageRequest,
    ConfigEdit,
    InitImage,
    layer_ops,
)
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.layer import ImageLayer
from pants_backend_oci.util_rules.oci_layout_builder import (
    AUTHOR,
    BUILT_BY,
    EPOCH,
    LAYER_DESCRIPTOR,
)


def test_config_edit_only_sets_given_fields() -> None:
    assert ConfigEdit().to_json() == {"op": "config"}
    assert ConfigEdit(
        env=("A=1",),
        entrypoint=("/bin/sh",),
        cmd=(),
        user="nonroot",
        clear=("config.cmd",),
        history=True,
    ).to_json() == {
        "op": "config",
        "clear": ["config.cmd"],
        "env": ["A=1"],
        "entrypoint": ["/bin/sh"],
        "cmd": [],
        "user": "nonroot",
        "history": {"created": EPOCH, "created_by": f"{AUTHOR} config", "author": AUTHOR},
    }


def test_init_image() -> None:
    assert InitImage("arm64", "linux").to_json() == {"op": "init", "architecture": "arm64", "os": "linux"}


def test_layer_ops_add_the_layer_then_its_config() -> None:
    address = Address("app", target_name="layer")
    layer = ImageLayer(address, EMPTY_DIGEST, "app/layer.tar", entrypoint="/app/main.pex")

    add, config = layer_ops(layer, "layers/0/compressed/layer.json")

    history = f"Layer target: {address}"
    assert add == {
        "op": "add_blob",
        "path": "layers/0/compressed/layer.json",
        "history": {"created": EPOCH, "created_by": history, "author": AUTHOR, "comment": history},
    }
    assert config == {
        "op": "config",
        "env": [BUILT_BY],
        "entrypoint": ["/app/main.pex"],
        "author": AUTHOR,
        "created": EPOCH,
    }
    assert "entrypoint" not in layer_ops(ImageLayer(address, EMPTY_DIGEST, "x.tar"), "x.tar")[1]


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *assemble.rules(),
            *external_tool.rules(),
            *adhoc_binaries.rules(),
            *sandboxed.rules(),
            *process.rules(),
            QueryRule(FallibleImageBundle, [AssembleImageRequest]),
            QueryRule(Snapshot, [Digest]),
            QueryRule(DigestContents, [Digest]),
        ],
    )


def compressed_layer(rule_runner: RuleRunner, tmp_path: Path, name: str, blob: bool = True) -> ImageLayer:
    """A layer already compressed to a blob and descriptor under `compressed/`."""
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as archive:
        info = tarfile.TarInfo(name)
        archive.addfile(info, io.BytesIO())
    Path(tmp_path, f"{name}.tar").write_bytes(tar.getvalue())
    out = tmp_path / name
    digest = oci_layout_builder.compress_to_blob(str(tmp_path / f"{name}.tar"), str(out))
    files = {f"compressed/{LAYER_DESCRIPTOR}": (out / LAYER_DESCRIPTOR).read_text()}
    if blob:
        files[f"compressed/blobs/sha256/{digest[7:]}"] = (out / "blobs" / "sha256" / digest[7:]).read_bytes()
    snapshot = rule_runner.make_snapshot(files)
    return ImageLayer(
        Address("", target_name=name),
        snapshot.digest,
        f"{name}.tar",
        descriptor=f"compressed/{LAYER_DESCRIPTOR}",
    )


def assembled(rule_runner: RuleRunner, base: ImageBundle | None, *ops) -> ImageBundle:
    result = rule_runner.request(FallibleImageBundle, [AssembleImageRequest(base, ops, "test")])
    assert result.output is not None, result.stderr
    return result.output


def layout_files(rule_runner: RuleRunner, digest: Digest) -> set[str]:
    return set(rule_runner.request(Snapshot, [digest]).files)


def image_blobs(rule_runner: RuleRunner, bundle: ImageBundle) -> tuple[str, str, list[str]]:
    contents = {c.path: c.content for c in rule_runner.request(DigestContents, [bundle.metadata])}
    manifest = json.loads(contents[f"build/blobs/sha256/{bundle.image_sha[7:]}"])
    return bundle.image_sha, manifest["config"]["digest"], [layer["digest"] for layer in manifest["layers"]]


def blob_files(*digests: str) -> set[str]:
    return {f"build/blobs/sha256/{digest[7:]}" for digest in digests}


METADATA_FILES = {"build/index.json", "build/oci-layout"}


def test_assembly_merges_blobs_outside_the_process(rule_runner: RuleRunner, tmp_path: Path) -> None:
    base = assembled(
        rule_runner, None, InitImage("amd64", "linux"), compressed_layer(rule_runner, tmp_path, "a")
    )
    manifest, config, (a,) = image_blobs(rule_runner, base)
    assert layout_files(rule_runner, base.digest) == METADATA_FILES | blob_files(manifest, config, a)
    # The process only ever sees and writes metadata.
    assert layout_files(rule_runner, base.metadata) == METADATA_FILES | blob_files(manifest, config)

    image = assembled(
        rule_runner,
        base,
        compressed_layer(rule_runner, tmp_path, "b"),
        ConfigEdit(user="nonroot", history=True),
    )
    manifest, config, layers = image_blobs(rule_runner, image)
    assert layers[0] == a
    # The base's layer blob is carried over, its old manifest and config dropped.
    assert layout_files(rule_runner, image.digest) == METADATA_FILES | blob_files(manifest, config, *layers)
    assert layout_files(rule_runner, image.metadata) == METADATA_FILES | blob_files(manifest, config)


def test_assembly_fails_on_a_missing_layer_blob(rule_runner: RuleRunner, tmp_path: Path) -> None:
    layer = compressed_layer(rule_runner, tmp_path, "a", blob=False)

    with pytest.raises(ExecutionError, match="blobs/sha256"):
        assembled(rule_runner, None, InitImage("amd64", "linux"), layer)
