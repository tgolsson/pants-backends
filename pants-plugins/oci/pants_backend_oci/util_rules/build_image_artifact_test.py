# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from types import SimpleNamespace

from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.targets import ImageBuildStep
from pants_backend_oci.util_rules.assemble import ConfigEdit
from pants_backend_oci.util_rules.build_image_artifact import (
    ImageArtifactBuildFieldSet,
    ImageArtifactBuildRequest,
    build_image_artifact,
)
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.layer import ImageLayer

BASE = ImageBundle(EMPTY_DIGEST, "sha256:base", True, metadata=EMPTY_DIGEST)
ASSEMBLED = ImageBundle(EMPTY_DIGEST, "sha256:assembled", True, metadata=EMPTY_DIGEST)


def _run(packages, base_bundle):
    address = Address("step")
    fields = {"base": ["//:base"], "commands": ["true"], "outputs": ["out"], "env": ["A=1"]}
    if packages:
        fields["packages"] = list(packages)
    target = ImageBuildStep(fields, address)
    dependencies = tuple(SimpleNamespace(address=Address("", target_name=p)) for p in packages)
    calls = {"layers": [], "assemble": [], "files": []}

    def build_image_layer(request, **kwargs):
        calls["layers"].append(request.target)
        return ImageLayer(request.target.address, EMPTY_DIGEST, "layer.tar")

    def assemble_image(request, **kwargs):
        calls["assemble"].append(request)
        return FallibleImageBundle(ASSEMBLED)

    def image_files(request, **kwargs):
        calls["files"].append(request)
        return SimpleNamespace(output_digest=EMPTY_DIGEST)

    result = run_rule_with_mocks(
        build_image_artifact,
        rule_args=[ImageArtifactBuildRequest(ImageArtifactBuildFieldSet.create(target))],
        mock_calls={
            "pants.engine.internals.graph.resolve_unparsed_address_inputs": lambda *args, **kwargs: (
                Address("", target_name="base"),
            ),
            "pants.engine.internals.graph.resolve_target": lambda *args, **kwargs: SimpleNamespace(
                target=object()
            ),
            "pants.engine.internals.graph.resolve_targets": lambda *args, **kwargs: dependencies,
            "pants_backend_oci.util_rules.image_bundle.ibr_to_fibr": lambda *args, **kwargs: SimpleNamespace(
                request=object()
            ),
            "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda *args,
            **kwargs: base_bundle,
            "pants_backend_oci.util_rules.layer.build_image_layer": build_image_layer,
            "pants_backend_oci.util_rules.assemble.assemble_image": assemble_image,
            "pants_backend_oci.util_rules.run.image_files": image_files,
        },
    )
    return result, dependencies, calls


def test_failed_base_returns_dependency_failed():
    failed = FallibleImageBundle(None, exit_code=1, stderr="broken base")

    result, dependencies, calls = _run(["a", "b"], failed)

    assert isinstance(result, FallibleImageBundle)
    assert result.dependency_failed
    assert result.stderr == "broken base"
    # Layers are requested alongside the base, not after it succeeds.
    assert calls["layers"] == list(dependencies)
    assert calls["assemble"] == []
    assert calls["files"] == []


def test_layers_are_assembled_onto_the_base():
    result, dependencies, calls = _run(["a", "b"], FallibleImageBundle(BASE))

    assert calls["layers"] == list(dependencies)
    (assembled,) = calls["assemble"]
    assert assembled.base == BASE
    *layers, config = assembled.ops
    assert [layer.address for layer in layers] == [d.address for d in dependencies]
    assert config == ConfigEdit(env=("A=1",), history=True)
    (files,) = calls["files"]
    assert files.bundle == ASSEMBLED
    assert files.command == ("true",)


def test_without_packages_the_base_is_used_directly():
    _, _, calls = _run([], FallibleImageBundle(BASE))

    assert calls["layers"] == []
    assert calls["assemble"] == []
    (files,) = calls["files"]
    assert files.bundle == BASE
