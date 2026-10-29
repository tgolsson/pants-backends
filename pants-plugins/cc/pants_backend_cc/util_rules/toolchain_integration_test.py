from __future__ import annotations

import os
import shutil

import pytest
from pants.engine.addresses import Address
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register
from pants_backend_cc.testutil import toolchain_build, toolchains_build
from pants_backend_cc.util_rules.toolchain import CcToolchain, CcToolchainRequest
from pants_backend_platforms import register as platforms_register
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS, OTHER, platforms_build
from pants_backend_platforms.util_rules.platforms import TargetPlatform

HOST_PLATFORM = TargetPlatform(HOST, HOST_OS, HOST_ARCH)
OTHER_PLATFORM = TargetPlatform(OTHER, "windows", "x86_64")


@pytest.fixture
def runner() -> RuleRunner:
    return RuleRunner(
        rules=[*register.rules(), QueryRule(CcToolchain, [CcToolchainRequest])],
        target_types=[*platforms_register.target_types(), *register.target_types()],
    )


def _toolchain(runner: RuleRunner, build: str, platform: TargetPlatform, *options: str) -> CcToolchain:
    runner.write_files({"p/BUILD": build} if build else {})
    runner.set_options(list(options), env_inherit={"PATH"})
    return runner.request(CcToolchain, [CcToolchainRequest(platform)])


def test_missing_toolchain(runner: RuleRunner) -> None:
    build = platforms_build() + toolchain_build("native", HOST)
    with pytest.raises(
        ExecutionError,
        match=rf"Expected one `cc_toolchain` with host='{HOST}' and target='{OTHER}', found none\. "
        rf"Defined: \['{HOST} -> {HOST}'\]",
    ):
        _toolchain(runner, build, OTHER_PLATFORM)


@pytest.mark.parametrize("strict", [False, True])
def test_two_matching_toolchains(runner: RuleRunner, strict: bool) -> None:
    build = platforms_build() + toolchains_build() + toolchain_build("again", HOST)
    with pytest.raises(ExecutionError, match=rf"found \['p:again', 'p:to-{HOST}'\]"):
        _toolchain(runner, build, HOST_PLATFORM, f"--cc-toolchains-strict={strict}")


def test_cross_build_without_any_toolchain(runner: RuleRunner) -> None:
    with pytest.raises(ExecutionError, match=rf"Building for {OTHER} on {HOST} needs a `cc_toolchain`"):
        _toolchain(runner, platforms_build(), OTHER_PLATFORM)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="the implicit toolchain is gcc")
def test_native_pair_without_a_toolchain_is_implicit(runner: RuleRunner) -> None:
    toolchain = _toolchain(runner, platforms_build() + toolchain_build("cross", OTHER), HOST_PLATFORM)
    assert toolchain.address is None
    assert os.path.basename(toolchain.cc.path) == "gcc"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="the implicit toolchain is gcc")
def test_no_platforms_defined_means_an_implicit_native_toolchain(runner: RuleRunner) -> None:
    toolchain = _toolchain(runner, "", HOST_PLATFORM)
    assert toolchain.address is None
    assert os.path.basename(toolchain.cc.path) == "gcc"


def test_strict_toolchains_need_a_native_toolchain(runner: RuleRunner) -> None:
    build = platforms_build() + toolchain_build("cross", OTHER)
    with pytest.raises(ExecutionError, match=rf"target='{HOST}', found none\. .*`\[cc-toolchains\]\.strict`"):
        _toolchain(runner, build, HOST_PLATFORM, "--cc-toolchains-strict")
    cross = _toolchain(runner, build, OTHER_PLATFORM, "--cc-toolchains-strict")
    assert cross.address == Address("p", target_name="cross")

    with pytest.raises(
        ExecutionError, match=rf"Building for {HOST} on {HOST} needs a `cc_toolchain`.*cc-toolchains"
    ):
        _toolchain(runner, platforms_build(), HOST_PLATFORM, "--cc-toolchains-strict")


def test_toolchain_env(runner: RuleRunner) -> None:
    bash = shutil.which("bash")
    assert bash
    toolchain = _toolchain(runner, platforms_build() + toolchain_build("abs", OTHER, cc=bash), OTHER_PLATFORM)
    assert toolchain.cc.path == bash
    assert toolchain.env["PATH"].split(os.pathsep)[0] == os.path.dirname(bash)
    assert toolchain.env["__PANTS_CC_TOOLCHAIN"] == (
        f"{toolchain.cc.fingerprint},{toolchain.cxx.fingerprint},{toolchain.binutils_fingerprint}"
    )


def test_archiver_contents_are_in_the_fingerprint(runner: RuleRunner) -> None:
    build = platforms_build() + toolchain_build("a", HOST) + toolchain_build("b", OTHER, ar="cat")
    with_bash = _toolchain(runner, build, HOST_PLATFORM)
    with_cat = _toolchain(runner, build, OTHER_PLATFORM)
    assert with_bash.cc.fingerprint == with_cat.cc.fingerprint
    assert with_bash.binutils_fingerprint != with_cat.binutils_fingerprint


def test_relative_tool_path(runner: RuleRunner) -> None:
    build = platforms_build() + toolchain_build("rel", HOST, cc="bin/gcc")
    with pytest.raises(
        ExecutionError, match=r"`bin/gcc`: a tool is a name on the search path or an absolute path"
    ):
        _toolchain(runner, build, HOST_PLATFORM)


def test_unknown_platform_in_toolchain(runner: RuleRunner) -> None:
    build = platforms_build() + toolchains_build() + toolchain_build("typo", "windos")
    with pytest.raises(ExecutionError, match=r"the `target` field of p:typo: unknown platform `windos`"):
        _toolchain(runner, build, HOST_PLATFORM)


def test_host_option_chooses_the_native_toolchain(runner: RuleRunner) -> None:
    build = platforms_build() + f'platform(name="musl", os="{HOST_OS}", arch="{HOST_ARCH}")\n'
    build += toolchain_build("musl-native", "musl", host="musl")
    musl = TargetPlatform("musl", HOST_OS, HOST_ARCH)
    toolchain = _toolchain(runner, build, musl, "--platforms-host=musl")
    assert toolchain.address == Address("p", target_name="musl-native")


def test_no_platform_matches_the_host(runner: RuleRunner) -> None:
    build = f'platform(name="{OTHER}", os="windows", arch="x86_64")\n' + toolchain_build("native", HOST)
    assert _toolchain(runner, build, HOST_PLATFORM).address == Address("p", target_name="native")
