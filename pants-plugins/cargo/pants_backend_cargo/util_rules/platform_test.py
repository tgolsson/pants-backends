"""The Rust target a platform builds as, the toolchain cargo gets for it, and the targets the
session's toolchain install covers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pants.engine.fs import Digest, DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.testing import HOST_PLATFORM, RUST_VERSION, make_rule_runner
from pants_backend_cargo.util_rules.cargo import CargoProcessRequest
from pants_backend_cargo.util_rules.platform import (
    CargoPlatform,
    CargoPlatformRequest,
    RustTarget,
    RustTargetRequest,
    platform_triple,
)
from pants_backend_cargo.util_rules.rustup import (
    RustToolchain,
    RustToolchainRequest,
    RustVersion,
    machine_toolchain_request,
)

from pants_backend_cc.testutil import toolchains_build
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS, OTHER, platforms_build
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform

MACHINE_TRIPLE = platform_triple(Platform.create_for_localhost())
WINDOWS = TargetPlatform(OTHER, "windows", "x86_64")
NO_TRIPLE = 'platform(name="arm-windows", os="windows", arch="aarch64")\n'


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(RustTarget, [RustTargetRequest]),
            QueryRule(CargoPlatform, [CargoPlatformRequest]),
            QueryRule(KnownPlatforms, []),
            QueryRule(RustToolchainRequest, []),
            QueryRule(RustVersion, []),
            QueryRule(Process, [CargoProcessRequest]),
            QueryRule(DigestContents, [Digest]),
        ]
    )


def _triple(rule_runner: RuleRunner, platform: TargetPlatform) -> str:
    return rule_runner.request(RustTarget, [RustTargetRequest(platform)]).triple


def test_rust_target_from_os_and_arch_or_the_field(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "BUILD": (
                platforms_build()
                + 'platform(name="llvm", os="windows", arch="x86_64", '
                + 'rust_target="x86_64-pc-windows-gnullvm")\n'
                + NO_TRIPLE
            ),
        }
    )
    host = rule_runner.request(KnownPlatforms, []).host
    assert _triple(rule_runner, host) == MACHINE_TRIPLE
    assert _triple(rule_runner, WINDOWS) == "x86_64-pc-windows-gnu"
    llvm = TargetPlatform("llvm", "windows", "x86_64")
    assert _triple(rule_runner, llvm) == "x86_64-pc-windows-gnullvm"
    with pytest.raises(ExecutionError, match=r"`arm-windows` \(windows, aarch64\): set its"):
        _triple(rule_runner, TargetPlatform("arm-windows", "windows", "aarch64"))


def test_implicit_host_takes_the_default(rule_runner: RuleRunner) -> None:
    known = rule_runner.request(KnownPlatforms, [])
    assert known.host.name == HOST
    assert _triple(rule_runner, known.host) == MACHINE_TRIPLE


def _names(triple: str) -> set[str]:
    under = triple.replace("-", "_")
    return {f"CARGO_TARGET_{under.upper()}_LINKER", *(f"{t}_{under}" for t in ("CC", "CXX", "AR"))}


def test_cross_toolchain_env(rule_runner: RuleRunner) -> None:
    # Every tool is `bash` but the cross archiver, so the two fingerprints differ.
    rule_runner.write_files(
        {
            "BUILD": (
                platforms_build()
                + f'cc_toolchain(name="h", host="{HOST}", target="{HOST}", cc="bash", cxx="bash", '
                + 'ar="bash")\n'
                + f'cc_toolchain(name="w", host="{HOST}", target="{OTHER}", cc="bash", cxx="bash", '
                + 'ar="cat")\n'
            )
        }
    )
    cross = rule_runner.request(CargoPlatform, [CargoPlatformRequest(WINDOWS)])
    host = rule_runner.request(CargoPlatform, [CargoPlatformRequest(HOST_PLATFORM)])
    assert cross.target.triple != cross.machine_triple
    assert host.target.triple == host.machine_triple

    env = dict(cross.env)
    fingerprint = env.pop("__PANTS_CC_TOOLCHAIN")
    assert set(env) == _names("x86_64-pc-windows-gnu") | _names(MACHINE_TRIPLE)
    for key, path in env.items():
        tool = "cat" if key == "AR_x86_64_pc_windows_gnu" else "bash"
        assert os.path.isabs(path) and os.path.basename(path) == tool, (key, path)
    # The build scripts' toolchain is in the cache key too.
    assert fingerprint.split(";") == [
        cross.cc.env["__PANTS_CC_TOOLCHAIN"],
        cross.host_cc.env["__PANTS_CC_TOOLCHAIN"],
    ]

    env = dict(host.env)
    assert env.pop("__PANTS_CC_TOOLCHAIN") == host.cc.env["__PANTS_CC_TOOLCHAIN"]
    assert set(env) == _names(MACHINE_TRIPLE)
    assert host.tools == ()
    # From the search path, when the machine has MinGW.
    assert {os.path.basename(t.path) for t in cross.tools} <= {"x86_64-w64-mingw32-dlltool"}


def test_native_platform_keeps_its_own_toolchain(rule_runner: RuleRunner) -> None:
    # A second platform for the machine itself, whose toolchain differs from the host's in its
    # archiver: its toolchain, not the host's, is the one under the machine's triple.
    rule_runner.write_files(
        {
            "BUILD": (
                platforms_build()
                + f'platform(name="native", os="{HOST_OS}", arch="{HOST_ARCH}")\n'
                + f'cc_toolchain(name="h", host="{HOST}", target="{HOST}", cc="bash", cxx="bash", '
                + 'ar="bash")\n'
                + f'cc_toolchain(name="n", host="{HOST}", target="native", cc="bash", cxx="bash", '
                + 'ar="cat")\n'
            )
        }
    )
    rule_runner.set_options([f"--platforms-host={HOST}"])
    native = rule_runner.request(
        CargoPlatform, [CargoPlatformRequest(TargetPlatform("native", HOST_OS, HOST_ARCH))]
    )
    env = dict(native.env)
    assert env.pop("__PANTS_CC_TOOLCHAIN") == native.cc.env["__PANTS_CC_TOOLCHAIN"]
    assert set(env) == _names(MACHINE_TRIPLE)
    assert os.path.basename(env[f"AR_{MACHINE_TRIPLE.replace('-', '_')}"]) == "cat"


def test_dlltool_next_to_the_windows_gnu_compiler(rule_runner: RuleRunner, tmp_path: Path) -> None:
    for name in ("x86_64-w64-mingw32-gcc-win32", "x86_64-w64-mingw32-dlltool"):
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\necho fake\n")
        tool.chmod(0o755)
    gcc = tmp_path / "x86_64-w64-mingw32-gcc-win32"
    build = (
        platforms_build()
        + f'cc_toolchain(name="w", host="{HOST}", target="{OTHER}", cc="{gcc}", cxx="{gcc}", '
        'ar="bash")\n'
        f'cc_toolchain(name="h", host="{HOST}", target="{HOST}", cc="{gcc}", cxx="{gcc}", '
        'ar="bash")\n'
    )
    rule_runner.write_files({"BUILD": build})
    cross = rule_runner.request(CargoPlatform, [CargoPlatformRequest(WINDOWS)])
    assert [t.path for t in cross.tools] == [str(tmp_path / "x86_64-w64-mingw32-dlltool")]
    # The same compiler for a non-Windows triple brings no dlltool.
    host = rule_runner.request(CargoPlatform, [CargoPlatformRequest(HOST_PLATFORM)])
    assert host.tools == ()


def _shims(rule_runner: RuleRunner, process: Process) -> set[str]:
    return {
        os.path.basename(fc.path)
        for digest in process.immutable_input_digests.values()
        for fc in rule_runner.request(DigestContents, [digest])
    }


def test_host_process_is_unchanged_but_for_the_toolchain(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({"BUILD": platforms_build() + toolchains_build()})
    host = rule_runner.request(CargoPlatform, [CargoPlatformRequest(HOST_PLATFORM)])
    toolchain = RustToolchain("tc", RUST_VERSION, (MACHINE_TRIPLE,))
    bare = rule_runner.request(Process, [CargoProcessRequest(toolchain, ("build",), "build")])
    process = rule_runner.request(
        Process,
        [CargoProcessRequest(toolchain, ("build",), "build", env=host.env, tool_paths=host.tools)],
    )
    assert process.argv == bare.argv
    assert dict(process.env) == {**bare.env, **host.env}
    assert set(host.env) == _names(MACHINE_TRIPLE) | {"__PANTS_CC_TOOLCHAIN"}
    assert process.immutable_input_digests == bare.immutable_input_digests
    assert not any("dlltool" in name for name in _shims(rule_runner, process))


def test_one_install_covers_every_enabled_platform(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({"BUILD": platforms_build() + NO_TRIPLE})
    version = rule_runner.request(RustVersion, [])
    machine = machine_toolchain_request(version, Platform.create_for_localhost())
    # Nothing enabled: what target generation installs, so a session installs once.
    assert rule_runner.request(RustToolchainRequest, []) == machine
    assert machine.targets == (MACHINE_TRIPLE,)

    # A platform without a triple is left out rather than failing every cargo goal.
    rule_runner.set_options(
        [
            f"--rustup-rust-version={RUST_VERSION}",
            f"--platforms-enabled=['{OTHER}', 'arm-windows']",
        ],
        env_inherit={"PATH"},
    )
    request = rule_runner.request(RustToolchainRequest, [])
    assert request.targets == (MACHINE_TRIPLE, "x86_64-pc-windows-gnu")
