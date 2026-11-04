from __future__ import annotations

import pytest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.subsystems import KacheTool, RustupTool
from pants_backend_cargo.testing import make_rule_runner
from pants_backend_cargo.util_rules.rustup import RustVersion

PLATFORMS = (
    Platform.linux_arm64,
    Platform.linux_x86_64,
    Platform.macos_arm64,
    Platform.macos_x86_64,
)


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(DownloadedExternalTool, [ExternalToolRequest]),
            QueryRule(RustupTool, []),
            QueryRule(KacheTool, []),
            QueryRule(RustVersion, []),
        ]
    )


@pytest.mark.parametrize("platform", PLATFORMS)
def test_download_rustup(rule_runner: RuleRunner, platform: Platform) -> None:
    rustup = rule_runner.request(RustupTool, [])
    rule_runner.request(DownloadedExternalTool, [rustup.get_request(platform)])


@pytest.mark.parametrize("platform", PLATFORMS)
def test_download_kache(rule_runner: RuleRunner, platform: Platform) -> None:
    kache = rule_runner.request(KacheTool, [])
    downloaded = rule_runner.request(DownloadedExternalTool, [kache.get_request(platform)])
    assert downloaded.exe == "./kache"


def test_rust_version_from_option(rule_runner: RuleRunner) -> None:
    assert rule_runner.request(RustVersion, []) == RustVersion("1.97.0", ("clippy", "rustfmt"))


def test_rust_version_from_toolchain_file(rule_runner: RuleRunner) -> None:
    rule_runner.set_options([], env_inherit={"PATH"})
    rule_runner.write_files(
        {"rust-toolchain.toml": '[toolchain]\nchannel = "1.90.0"\ncomponents = ["clippy"]\n'}
    )
    assert rule_runner.request(RustVersion, []) == RustVersion("1.90.0", ("clippy", "rustfmt"))


def test_rust_version_must_be_exact(rule_runner: RuleRunner) -> None:
    rule_runner.set_options([], env_inherit={"PATH"})
    rule_runner.write_files({"rust-toolchain.toml": '[toolchain]\nchannel = "stable"\n'})
    with pytest.raises(Exception, match="must be exact"):
        rule_runner.request(RustVersion, [])


def test_rust_version_missing(rule_runner: RuleRunner) -> None:
    rule_runner.set_options([], env_inherit={"PATH"})
    with pytest.raises(Exception, match="No Rust version"):
        rule_runner.request(RustVersion, [])
