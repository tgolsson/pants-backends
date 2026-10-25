# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
from pants.core.util_rules import system_binaries
from pants.engine import process
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.process import Process, ProcessResult
from pants.testutil.rule_runner import QueryRule, RuleRunner

from pants_backend_oci.util_rules import (
    builder_main,
    deb_layer_builder,
    helper_layer_builder,
    layer_index,
    oci_layout_builder,
    sandboxed,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder, sandboxed_modules


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


def test_builder_dispatches_every_bundled_module() -> None:
    bundled = {m.__name__.rpartition(".")[2] for m in sandboxed._MODULES if m is not builder_main}
    assert set(builder_main.MODULES) == bundled


def test_builder_runs_without_the_cache_home(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    module = SimpleNamespace(
        main=lambda args: seen.update(args=args, prog=sys.argv[0], home=os.environ.get("HOME")) or 0
    )
    monkeypatch.setattr(builder_main.importlib, "import_module", lambda name: module)
    monkeypatch.setattr(sys, "argv", ["__oci_builder/oci-builder", "layer_index", "x"])
    monkeypatch.setenv("HOME", "/sandbox/.oci_builder")
    monkeypatch.setenv(builder_main.CACHE_HOME, "1")

    assert builder_main.main() == 0
    assert seen == {"args": ["x"], "prog": "oci-builder layer_index", "home": None}
    assert builder_main.CACHE_HOME not in os.environ


def test_builder_process_unpacks_into_a_named_cache() -> None:
    process = OciBuilder(EMPTY_DIGEST).process(
        layer_index,
        "check",
        env={"PATH": "/bin"},
        append_only_caches={"other": ".other"},
        description="Check",
    )

    assert process.argv == (OciBuilder.exe, "layer_index", "check")
    assert process.immutable_input_digests == {"__oci_builder": EMPTY_DIGEST}
    assert process.append_only_caches == {"other": ".other", "oci_builder": ".oci_builder"}
    # `{chroot}` is filled in at run time, so the cache key names no host path.
    assert process.env == {"PATH": "/bin", "HOME": "{chroot}/.oci_builder", builder_main.CACHE_HOME: "1"}


def test_oci_builder_runs_a_builder_module() -> None:
    rule_runner = RuleRunner(
        rules=[
            *sandboxed.rules(),
            *system_binaries.rules(),
            *process.rules(),
            QueryRule(OciBuilder, []),
            QueryRule(ProcessResult, [Process]),
        ],
    )
    rule_runner.set_options([], env_inherit={"PATH"})
    builder = rule_runner.request(OciBuilder, [])
    # The same path in every sandbox, so the scie's venv setup is shared.
    assert builder.home == "/proc/self/cwd/.oci_builder"

    # Relative, so the same on every host.
    assert builder.argv(oci_layout_builder, "x") == (OciBuilder.exe, "oci_layout_builder", "x")
    result = rule_runner.request(
        ProcessResult,
        [builder.process(layer_index, "--help", description="Run oci-builder")],
    )
    assert b"usage:" in result.stdout
