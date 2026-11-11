# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.language_target.python import (
    BuildPythonImageFieldSet,
    BuildPythonImageRequest,
    PythonImageBuild,
    build_python_image,
)
from pants_backend_oci.util_rules.assemble import ConfigEdit
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.layer import FallibleImageLayer, ImageLayer

BASE = ImageBundle(EMPTY_DIGEST, "sha256:base", True, metadata=EMPTY_DIGEST)
ASSEMBLED = FallibleImageBundle(ImageBundle(EMPTY_DIGEST, "sha256:assembled", True, metadata=EMPTY_DIGEST))
BROKEN_BASE = FallibleImageBundle(None, exit_code=1, stderr="broken base")


def _run(base_bundle, failing=None):
    target = PythonImageBuild({"base": ["//:base"], "packages": [":a", ":b"]}, Address("img"))
    dependencies = tuple(SimpleNamespace(address=Address("", target_name=name)) for name in ("a", "b"))
    calls = {"layers": [], "assemble": []}

    def build_image_layer_fallible(request, **kwargs):
        calls["layers"].append(request.target)
        name = request.target.address.target_name
        error = (failing or {}).get(name)
        if error is not None:
            return FallibleImageLayer(None, error)
        entrypoint = "/app.pex" if name == "b" else None
        return FallibleImageLayer(ImageLayer(request.target.address, EMPTY_DIGEST, "layer.tar", entrypoint))

    def assemble_image(request, **kwargs):
        calls["assemble"].append(request)
        return ASSEMBLED

    result = run_rule_with_mocks(
        build_python_image,
        rule_args=[BuildPythonImageRequest(BuildPythonImageFieldSet.create(target))],
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
            "pants_backend_oci.util_rules.layer.build_image_layer_fallible": build_image_layer_fallible,
            "pants_backend_oci.util_rules.assemble.assemble_image": assemble_image,
        },
    )
    return result, calls, dependencies


def test_failed_base_is_reported_over_a_failed_layer():
    result, calls, dependencies = _run(BROKEN_BASE, failing={"b": RuntimeError("layer boom")})

    assert result.output is None
    assert result.dependency_failed
    assert result.stderr == "broken base"
    assert calls["layers"] == list(dependencies)
    assert calls["assemble"] == []


def test_failed_layer_raises_its_own_error_when_the_base_is_fine():
    error = RuntimeError("layer boom")
    with pytest.raises(RuntimeError) as raised:
        _run(FallibleImageBundle(BASE), failing={"b": error})
    assert raised.value is error


def test_layers_are_assembled_with_the_pex_entrypoint():
    result, calls, dependencies = _run(FallibleImageBundle(BASE))

    assert result is ASSEMBLED
    (assembled,) = calls["assemble"]
    assert assembled.base == BASE
    *layers, config = assembled.ops
    assert [layer.address for layer in layers] == [d.address for d in dependencies]
    assert config == ConfigEdit(entrypoint=("python", "/app.pex"), history=True)
