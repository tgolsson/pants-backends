# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from pants.core.util_rules import system_binaries
from pants.engine import process
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.process import Process, ProcessResult
from pants.testutil.rule_runner import QueryRule, RuleRunner

from pants_backend_distroless import sandboxed, subsystem
from pants_backend_distroless.sandboxed import AptResolver, resolver_sources


def test_resolver_sources_keep_their_package_path() -> None:
    files = {f.path: f.content for f in resolver_sources()}

    assert set(files) == {
        "pants_backend_distroless/__init__.py",
        "pants_backend_distroless/apt_resolver.py",
        "pants_backend_distroless/openpgp.py",
    }
    # The package is empty, so importing the resolver doesn't import the plugin and Pants.
    assert files["pants_backend_distroless/__init__.py"] == b""
    assert b"def main(" in files["pants_backend_distroless/apt_resolver.py"]


def test_resolver_process_unpacks_into_a_named_cache() -> None:
    process = AptResolver(EMPTY_DIGEST).process("--request", "r.json", description="Resolve")

    assert process.argv == (AptResolver.exe, "--request", "r.json")
    assert process.immutable_input_digests == {"__apt_resolver": EMPTY_DIGEST}
    assert process.append_only_caches == {"apt_resolver": ".apt_resolver"}
    # `{chroot}` is filled in at run time, so the cache key names no host path.
    assert process.env == {"HOME": "{chroot}/.apt_resolver"}


def test_apt_resolver_runs() -> None:
    rule_runner = RuleRunner(
        rules=[
            *sandboxed.rules(),
            *subsystem.rules(),
            *system_binaries.rules(),
            *process.rules(),
            QueryRule(AptResolver, []),
            QueryRule(ProcessResult, [Process]),
        ],
    )
    rule_runner.set_options([], env_inherit={"PATH"})
    resolver = rule_runner.request(AptResolver, [])
    # The same path in every sandbox, so the scie's venv setup is shared.
    assert resolver.home == "/proc/self/cwd/.apt_resolver"

    result = rule_runner.request(ProcessResult, [resolver.process("--help", description="Run apt-resolver")])
    assert b"--allow-unsigned" in result.stdout
