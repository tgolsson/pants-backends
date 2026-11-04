"""`cargo build` of one binary for a platform."""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.fs import Digest, RemovePrefix
from pants.engine.intrinsics import remove_prefix
from pants.engine.process import fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.frozendict import FrozenDict
from pants_backend_cargo.util_rules.cargo import TARGET_DIR, CargoProcessRequest, manifest_path
from pants_backend_cargo.util_rules.platform import CargoPlatformRequest, RustTarget, cargo_platform
from pants_backend_cargo.util_rules.rustup import install_rust_toolchain
from pants_backend_cargo.util_rules.sandbox import CargoSourcesRequest, cargo_sources

from pants_backend_platforms.util_rules.platforms import TargetPlatform


@dataclass(frozen=True)
class CargoBinaryRequest:
    address: Address
    binary_name: str
    release: bool
    platform: TargetPlatform


@dataclass(frozen=True)
class CargoBinary:
    """The built binary, alone at the root of the digest."""

    digest: Digest
    name: str


@dataclass(frozen=True)
class CargoBuildInvocation:
    command: tuple[str, ...]
    output_dir: str
    output_name: str


def cargo_build_invocation(
    request: CargoBinaryRequest, target: RustTarget, host_triple: str
) -> CargoBuildInvocation:
    """No `--target` for the machine's own triple, so the host build's command and output
    path are those of a plain `cargo build`."""
    profile = "release" if request.release else "debug"
    cross = target.triple != host_triple
    output_dir = f"{TARGET_DIR}/{target.triple}/{profile}" if cross else f"{TARGET_DIR}/{profile}"
    return CargoBuildInvocation(
        command=(
            "build",
            "--locked",
            f"--manifest-path={manifest_path(request.address.spec_path)}",
            f"--bin={request.binary_name}",
            *(("--release",) if request.release else ()),
            *((f"--target={target.triple}",) if cross else ()),
        ),
        output_dir=output_dir,
        output_name=f"{request.binary_name}{target.exe_suffix}",
    )


@rule(desc="Build a cargo binary")
async def build_cargo_binary(request: CargoBinaryRequest) -> CargoBinary:
    toolchain, cargo, sources = await concurrently(
        install_rust_toolchain(**implicitly()),
        cargo_platform(CargoPlatformRequest(request.platform)),
        cargo_sources(
            CargoSourcesRequest(
                frozenset([request.address]),
                include_packages=True,
                platform=request.platform,
                dependent=request.address,
            )
        ),
    )
    invocation = cargo_build_invocation(request, cargo.target, cargo.machine_triple)
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            CargoProcessRequest(
                toolchain,
                invocation.command,
                description=f"cargo build {request.address} for {request.platform.name}",
                digest=sources.digest,
                output_files=(f"{invocation.output_dir}/{invocation.output_name}",),
                env=FrozenDict({**sources.env, **cargo.env}),
                tool_paths=cargo.tools,
            )
        )
    )
    digest = await remove_prefix(RemovePrefix(result.output_digest, invocation.output_dir))
    return CargoBinary(digest, invocation.output_name)


def rules():
    return collect_rules()
