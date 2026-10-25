# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""The `oci-builder` entry point: `oci-builder <module> args...` runs that builder module's `main`.

Stdlib only, like the builders themselves.
"""

from __future__ import annotations

import importlib
import os
import sys

MODULES = (
    "deb_layer_builder",
    "helper_layer_builder",
    "layer_index",
    "oci_layout_builder",
    "podman_builder",
    "podman_runner",
)

# Set when `HOME` only points the scie at its cache; the builders themselves run without one.
CACHE_HOME = "__OCI_BUILDER_CACHE_HOME"


def main() -> int:
    if os.environ.pop(CACHE_HOME, None):
        os.environ.pop("HOME", None)
    if len(sys.argv) < 2 or sys.argv[1] not in MODULES:
        print(f"usage: {sys.argv[0]} {{{','.join(MODULES)}}} [args...]", file=sys.stderr)
        return 2
    name = sys.argv[1]
    module = importlib.import_module(f"pants_backend_oci.util_rules.{name}")
    # Usage and errors name the module, as `argparse` takes its `prog` from here.
    sys.argv[0] = f"{os.path.basename(sys.argv[0])} {name}"
    return module.main(sys.argv[2:])
