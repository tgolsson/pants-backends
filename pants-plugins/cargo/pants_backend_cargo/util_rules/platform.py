"""What building for a `platform` (pants-backends' pants_backend_platforms) means to cargo: the
Rust target triple, and the `cc_toolchain` (pants_backend_cc) that links for it and compiles
cc-rs's C."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BinaryPath,
    BinaryPathRequest,
    SystemBinariesSubsystem,
    find_binary,
)
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import AllTargets
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants_backend_cargo.subsystems import DEFAULT_TRIPLES, PLATFORM_TRIPLES
from pants_backend_cargo.target_types import RustTargetField

from pants_backend_cc.util_rules.toolchain import (
    CcToolchain,
    CcToolchainRequest,
    resolve_cc_toolchain,
)
from pants_backend_platforms.target_types import PlatformTarget
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform

TOOLCHAIN_FINGERPRINT_ENV = "__PANTS_CC_TOOLCHAIN"


def platform_triple(platform: Platform) -> str:
    """The triple of the machine Pants runs on: what cargo builds for without `--target`."""
    return PLATFORM_TRIPLES[platform.value]


def rust_triple(platform: TargetPlatform, all_targets: AllTargets) -> str | None:
    """The platform's `rust_target`, else the default for its os and arch; the implicit host
    has no target and takes the default."""
    for tgt in all_targets:
        if isinstance(tgt, PlatformTarget) and tgt.address.target_name == platform.name:
            if tgt[RustTargetField].value:
                return tgt[RustTargetField].value
            break
    return DEFAULT_TRIPLES.get((platform.os, platform.arch))


def toolchain_env_names(triple: str) -> frozenset[str]:
    """The variables `CargoPlatform.env` sets for a triple, in both spellings cc-rs reads."""
    under = triple.replace("-", "_")
    return frozenset(
        {
            f"CARGO_TARGET_{under.upper()}_LINKER",
            *(f"{tool}_{t}" for tool in ("CC", "CXX", "AR") for t in (triple, under)),
        }
    )


@dataclass(frozen=True)
class RustTargetRequest:
    platform: TargetPlatform


@dataclass(frozen=True)
class RustTarget:
    platform: TargetPlatform
    triple: str

    @property
    def exe_suffix(self) -> str:
        return ".exe" if self.platform.os == "windows" else ""


@rule(desc="Find a platform's Rust target", level=LogLevel.DEBUG)
async def rust_target(request: RustTargetRequest, all_targets: AllTargets) -> RustTarget:
    platform = request.platform
    triple = rust_triple(platform, all_targets)
    if triple is None:
        raise ValueError(
            f"No default Rust target for the platform `{platform.name}` ({platform.os}, "
            f"{platform.arch}): set its `rust_target`."
        )
    return RustTarget(platform, triple)


@dataclass(frozen=True)
class CargoPlatformRequest:
    platform: TargetPlatform


@dataclass(frozen=True)
class CargoPlatform:
    target: RustTarget
    cc: CcToolchain
    machine_triple: str
    host_cc: CcToolchain
    tools: tuple[BinaryPath, ...]
    """What rustc runs by name for this target, to shim onto PATH."""

    @property
    def env(self) -> FrozenDict[str, str]:
        """The target's toolchain under its triple and, in a cross build, the host's under the
        machine's, which build scripts and proc macros are built for. PATH stays cargo's own."""
        # A native build for a platform with its own toolchain keeps that toolchain.
        toolchains = {self.target.triple: self.cc}
        toolchains.setdefault(self.machine_triple, self.host_cc)
        env = {}
        for triple, cc in toolchains.items():
            under = triple.replace("-", "_")
            env.update(
                {
                    f"CARGO_TARGET_{under.upper()}_LINKER": cc.cc.path,
                    f"CC_{under}": cc.cc.path,
                    f"CXX_{under}": cc.cxx.path,
                    f"AR_{under}": cc.ar.path,
                }
            )
        fingerprints = dict.fromkeys(cc.env[TOOLCHAIN_FINGERPRINT_ENV] for cc in toolchains.values())
        env[TOOLCHAIN_FINGERPRINT_ENV] = ";".join(fingerprints)
        return FrozenDict(env)


@rule(desc="Resolve the toolchain cargo builds a platform with", level=LogLevel.DEBUG)
async def cargo_platform(
    request: CargoPlatformRequest,
    known: KnownPlatforms,
    platform: Platform,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> CargoPlatform:
    target, cc, host_cc = await concurrently(
        rust_target(RustTargetRequest(request.platform), **implicitly()),
        resolve_cc_toolchain(CcToolchainRequest(request.platform), **implicitly()),
        resolve_cc_toolchain(CcToolchainRequest(known.host), **implicitly()),
    )
    tools: tuple[BinaryPath, ...] = ()
    # A `raw-dylib` crate (`windows-sys`) makes rustc run `<arch>-w64-mingw32-dlltool` by
    # name; the one next to the compiler belongs with it.
    if target.triple.endswith("-windows-gnu"):
        name = f"{target.triple.split('-')[0]}-w64-mingw32-dlltool"
        search_path = (os.path.dirname(cc.cc.path), *system_binaries.system_binary_paths)
        dlltool = await find_binary(
            BinaryPathRequest(binary_name=name, search_path=search_path), **implicitly()
        )
        if dlltool.first_path:
            tools = (dlltool.first_path,)
    return CargoPlatform(target, cc, platform_triple(platform), host_cc, tools)


def rules():
    return [*collect_rules(), PlatformTarget.register_plugin_field(RustTargetField)]
