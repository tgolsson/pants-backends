"""Shared test setup. The tests run cargo for real: rustup and the toolchain come from the
named caches, which the tests share with ordinary Pants runs on the machine so the toolchain
is installed once, not once per test."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pants.core import target_types as core_target_types
from pants.core.goals import package as core_package
from pants.core.goals import test as core_test
from pants.core.target_types import FilesGeneratorTarget
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.rules import QueryRule, Rule
from pants.engine.target import Target
from pants.engine.unions import UnionRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo import register
from pants_backend_cargo.backends.clippy import register as clippy_register

from pants_backend_cc.target_types import CcToolchainTarget
from pants_backend_platforms import register as platforms_register
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS
from pants_backend_platforms.util_rules.platforms import TargetPlatform

RUST_VERSION = "1.97.0"
# The implicit host, or a `platform` target named like it.
HOST_PLATFORM = TargetPlatform(HOST, HOST_OS, HOST_ARCH)


def make_rule_runner(
    query_rules: Iterable[QueryRule] = (),
    *,
    options: Iterable[str] = (),
    rules: Iterable[Rule | UnionRule] = (),
    target_types: Iterable[type[Target]] = (),
) -> RuleRunner:
    rule_runner = RuleRunner(
        rules=[
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *core_target_types.rules(),
            *core_package.rules(),
            *core_test.rules(),
            *env_vars.rules(),
            *register.rules(),
            *clippy_register.rules(),
            *query_rules,
            *rules,
        ],
        target_types=[
            *register.target_types(),
            *platforms_register.target_types(),
            CcToolchainTarget,
            FilesGeneratorTarget,
            *target_types,
        ],
        bootstrap_args=[f"--named-caches-dir={Path.home() / '.cache' / 'pants' / 'named_caches'}"],
    )
    rule_runner.set_options(
        [f"--rustup-rust-version={RUST_VERSION}", *options],
        env_inherit={"PATH"},
    )
    return rule_runner


def package_fixture(name: str, lib: str = "", main: str | None = None) -> dict[str, str]:
    """A `cargo_package` in `<name>/` with a library and, with `main`, a binary."""
    files = {
        f"{name}/BUILD": "cargo_package()",
        f"{name}/Cargo.toml": f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n',
        f"{name}/Cargo.lock": f'version = 4\n\n[[package]]\nname = "{name}"\nversion = "0.1.0"\n',
        f"{name}/src/lib.rs": lib,
    }
    if main is not None:
        files[f"{name}/src/main.rs"] = main
    return files
