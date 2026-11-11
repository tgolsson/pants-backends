# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.internals.selectors import Call

from pants_backend_oci.util_rules.layer import (
    FallibleImageLayer,
    ImageLayer,
    ImageLayerRequest,
    build_image_layer_fallible,
    layers_or_raise,
)

LAYER_A = ImageLayer(Address("", target_name="a"), EMPTY_DIGEST, "a.tar")
LAYER_B = ImageLayer(Address("", target_name="b"), EMPTY_DIGEST, "b.tar")


@dataclass(frozen=True)
class _Target:
    address: Address


def _start_fallible_build():
    """Runs `build_image_layer_fallible` up to its call of `build_image_layer`.

    `run_rule_with_mocks` raises a failing mock's exception outside the rule, so the coroutine is
    driven by hand to throw it in, as the engine does.
    """
    request = ImageLayerRequest(_Target(LAYER_A.address))
    coroutine = build_image_layer_fallible.rule.func(request)
    call = coroutine.send(None)
    assert isinstance(call, Call)
    assert call.rule_id == "pants_backend_oci.util_rules.layer.build_image_layer"
    assert request in call.inputs
    return coroutine


def test_fallible_build_wraps_the_layer():
    coroutine = _start_fallible_build()
    with pytest.raises(StopIteration) as stopped:
        coroutine.send(LAYER_A)
    assert stopped.value.value == FallibleImageLayer(LAYER_A)


def test_fallible_build_captures_the_layer_error():
    coroutine = _start_fallible_build()
    error = RuntimeError("layer boom")
    with pytest.raises(StopIteration) as stopped:
        coroutine.throw(error)
    assert stopped.value.value.output is None
    assert stopped.value.value.error is error


def test_layers_or_raise_returns_the_layers_in_order():
    assert layers_or_raise([FallibleImageLayer(LAYER_A), FallibleImageLayer(LAYER_B)]) == (
        LAYER_A,
        LAYER_B,
    )
    assert layers_or_raise([]) == ()


def test_layers_or_raise_reraises_the_first_original_error():
    first, second = RuntimeError("first"), ValueError("second")
    layers = [FallibleImageLayer(LAYER_A), FallibleImageLayer(None, first), FallibleImageLayer(None, second)]
    with pytest.raises(RuntimeError) as raised:
        layers_or_raise(layers)
    assert raised.value is first
