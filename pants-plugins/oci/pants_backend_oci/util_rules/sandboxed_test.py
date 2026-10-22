# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from pants_backend_oci.util_rules import deb_layer_builder, helper_layer_builder, oci_layout_builder
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules


def test_sandboxed_modules_keep_their_package_paths() -> None:
    files = {f.path: f.content for f in sandboxed_modules(deb_layer_builder, helper_layer_builder)}

    assert set(files) == {
        "pants_backend_oci/__init__.py",
        "pants_backend_oci/util_rules/__init__.py",
        "pants_backend_oci/util_rules/deb_layer_builder.py",
        "pants_backend_oci/util_rules/helper_layer_builder.py",
    }
    # The packages are empty, so importing a builder doesn't import the plugin and Pants.
    assert files["pants_backend_oci/__init__.py"] == b""
    assert files["pants_backend_oci/util_rules/__init__.py"] == b""
    assert b"def main(" in files["pants_backend_oci/util_rules/helper_layer_builder.py"]


def test_module_main_runs_the_module() -> None:
    assert module_main(oci_layout_builder) == (
        "import sys, pants_backend_oci.util_rules.oci_layout_builder as m; sys.exit(m.main(sys.argv[1:]))"
    )
