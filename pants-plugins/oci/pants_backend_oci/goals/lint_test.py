# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.goals.lint import DebLayerOverwritesRequest, lint_deb_layer_overwrites
from pants_backend_oci.targets import ImageBuild
from pants_backend_oci.util_rules.build_image_bundle import (
    BuildImageBundleFieldSet,
    ImageLayerRequests,
)
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.layer import FallibleImageLayer, ImageLayerRequest

BASE = ImageBundle(EMPTY_DIGEST, "sha256:base", True, metadata=EMPTY_DIGEST)
BROKEN_BASE = FallibleImageBundle(None, exit_code=1, stderr="broken base")


@dataclass(frozen=True)
class _DebLayer:
    address: Address

    def has_field(self, field) -> bool:
        return True


@dataclass(frozen=True)
class _Base:
    target: _DebLayer


def _run(base_bundle, layer_error):
    field_set = BuildImageBundleFieldSet.create(ImageBuild({"base": ["//:base"]}, Address("img")))
    layer_request = ImageLayerRequest(_DebLayer(Address("", target_name="deb")), old_style=False)
    requests = ImageLayerRequests(_Base(_DebLayer(Address("base"))), (layer_request,))
    layers = []

    def build_image_layer_fallible(request, **kwargs):
        layers.append(request)
        return FallibleImageLayer(None, layer_error)

    result = run_rule_with_mocks(
        lint_deb_layer_overwrites,
        rule_args=[
            DebLayerOverwritesRequest.Batch(
                "deb-layer-overwrites", (field_set,), SimpleNamespace(description=None)
            ),
            object(),
        ],
        mock_calls={
            "pants_backend_oci.util_rules.build_image_bundle.image_layer_requests": lambda *args,
            **kwargs: requests,
            "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda *args,
            **kwargs: base_bundle,
            "pants_backend_oci.util_rules.layer.build_image_layer_fallible": build_image_layer_fallible,
        },
    )
    return result, layers, layer_request


def test_failed_base_is_reported_over_a_failed_layer():
    result, layers, layer_request = _run(BROKEN_BASE, RuntimeError("layer boom"))

    assert result.exit_code == 1
    assert result.stderr == "broken base"
    assert layers == [layer_request]


def test_failed_layer_raises_its_own_error_when_the_base_is_fine():
    error = RuntimeError("layer boom")
    with pytest.raises(RuntimeError) as raised:
        _run(FallibleImageBundle(BASE), error)
    assert raised.value is error
