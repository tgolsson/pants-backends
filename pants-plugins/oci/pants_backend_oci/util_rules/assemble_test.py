# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST

from pants_backend_oci.util_rules.assemble import ConfigEdit, InitImage, layer_ops
from pants_backend_oci.util_rules.layer import ImageLayer
from pants_backend_oci.util_rules.oci_layout_builder import AUTHOR, BUILT_BY, EPOCH


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

    add, config = layer_ops(layer, "layers/0/app/layer.tar")

    history = f"Layer target: {address}"
    assert add == {
        "op": "add_layer",
        "path": "layers/0/app/layer.tar",
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
