"""Running scripted build steps, and finding the podman that runs them and `pants run`.

`podman_builder`, or the runner `[oci].runner` picks, does the work in a sandboxed process; these
rules only decide what it builds.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass

from pants.core.environments.target_types import EnvironmentTarget
from pants.core.util_rules.env_vars import environment_vars_subset
from pants.core.util_rules.system_binaries import (
    BinaryPath,
    BinaryPathRequest,
    SystemBinariesSubsystem,
    find_binary,
)
from pants.engine.addresses import Address
from pants.engine.env_vars import EnvironmentVarsRequest
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciRunner, OciSubsystem
from pants_backend_oci.util_rules import podman_builder
from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.layer import ImageLayer
from pants_backend_oci.util_rules.runner import (
    ContainerBuildRequest,
    container_build,
    runner_member,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder

_LINUX_ONLY = "OCI containers require local Linux podman; podman machine is not supported yet."

# Shared with `pants run`, which reaches the same named cache by its absolute path.
PODMAN_CACHE = "oci_podman"
PODMAN_STORE_VERSION = "v2"

_SCRIPTED_LAYER = "scripted-layer.tar"


class PodmanBinary(BinaryPath):
    pass


@rule(desc="Finding podman for OCI containers")
async def find_podman(
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
    platform: Platform,
    env_target: EnvironmentTarget,
) -> PodmanBinary:
    if platform not in (Platform.linux_x86_64, Platform.linux_arm64):
        raise ValueError(_LINUX_ONLY)
    request = BinaryPathRequest(binary_name="podman", search_path=system_binaries.system_binary_paths)
    paths = await find_binary(request, **implicitly())
    binary = paths.first_path_or_raise(
        request,
        rationale="run OCI images. Install podman: https://podman.io/docs/installation",
    )
    # Do not execute podman during discovery: even --version can initialize its host runtime. Its
    # hash is the fingerprint instead, so build steps rerun when podman is upgraded.
    sha256sum_request = BinaryPathRequest(
        binary_name="sha256sum", search_path=system_binaries.system_binary_paths
    )
    sha256sum = (await find_binary(sha256sum_request, **implicitly())).first_path_or_raise(
        sha256sum_request, rationale="fingerprint podman"
    )
    process = Process(
        (sha256sum.path, binary.path),
        description=f"Fingerprint {binary.path}",
        level=LogLevel.DEBUG,
        cache_scope=env_target.executable_search_path_cache_scope(),
    )
    result = await fallible_to_exec_result_or_raise(**implicitly({process: Process}))
    return PodmanBinary(binary.path, result.stdout.split()[0].decode())


@dataclass(frozen=True)
class PodmanBuildRequest(ContainerBuildRequest):
    """Run `podman_builder` with `args` on `bundle`'s layout."""

    runner = OciRunner.podman


@rule
async def podman_build(
    request: PodmanBuildRequest,
    podman: PodmanBinary,
    builder: OciBuilder,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> ProcessResult:
    # The user's runtime directory, as `pants run` sees it: the store's runroot is derived from it,
    # and must be the same for both.
    runtime = await environment_vars_subset(EnvironmentVarsRequest(["XDG_RUNTIME_DIR"]), **implicitly())
    process = builder.process(
        podman_builder,
        "--podman",
        podman.path,
        "--cache",
        f".{PODMAN_CACHE}/{PODMAN_STORE_VERSION}",
        *request.args,
        input_digest=request.bundle.digest,
        append_only_caches={PODMAN_CACHE: f".{PODMAN_CACHE}"},
        env={
            # Podman finds newuidmap, conmon and its OCI runtime on the PATH.
            "PATH": ":".join(system_binaries.system_binary_paths),
            # Unread: it puts the podman binary in the cache key, so an upgrade reruns build steps.
            "__OCI_PODMAN_FINGERPRINT": podman.fingerprint,
            **runtime,
        },
        output_files=request.output_files,
        output_directories=request.output_directories,
        description=request.description,
        level=LogLevel.DEBUG,
    )
    return await fallible_to_exec_result_or_raise(**implicitly({process: Process}))


def _argv(oci: OciSubsystem, command: str | tuple[str, ...]) -> list[str]:
    """A string runs through the shell; a list is exec form and runs as it is."""
    return [*oci.command_shell, command] if isinstance(command, str) else list(command)


def _run_args(oci: OciSubsystem, command: str | tuple[str, ...] | None) -> tuple[str, ...]:
    return ("--run", json.dumps(_argv(oci, command))) if command else ()


@dataclass(frozen=True)
class ScriptedLayerRequest:
    """The layer `command` adds when run in `bundle`, for the rules to append to it."""

    bundle: ImageBundle
    command: str | tuple[str, ...]
    address: Address


@rule(desc="Run scripted OCI build step", level=LogLevel.DEBUG)
async def run_scripted_layer(
    request: ScriptedLayerRequest, oci: OciSubsystem, union_membership: UnionMembership
) -> ImageLayer:
    build = runner_member(ContainerBuildRequest, oci, union_membership)(
        request.bundle,
        (*_run_args(oci, request.command), "--dest", _SCRIPTED_LAYER, "layer"),
        f"Run build step for {request.address}",
        output_files=(_SCRIPTED_LAYER,),
    )
    result = await container_build(**implicitly({build: ContainerBuildRequest}))
    command = request.command if isinstance(request.command, str) else shlex.join(request.command)
    return ImageLayer(
        request.address, result.output_digest, _SCRIPTED_LAYER, created_by=f"Build step: {command}"
    )


@dataclass(frozen=True)
class ImageFilesRequest:
    """`outputs` from `bundle`, after running `command` if given, as the tar `tar_name`."""

    address: Address
    tar_name: str
    bundle: ImageBundle
    command: str | tuple[str, ...] | None
    outputs: tuple[str, ...]
    exclude_patterns: tuple[str, ...] = ()
    description: str = "Copy files out of OCI image"


@rule(desc="Copy files out of OCI image", level=LogLevel.DEBUG)
async def image_files(
    request: ImageFilesRequest, oci: OciSubsystem, union_membership: UnionMembership
) -> ProcessResult:
    if not request.outputs:
        raise ValueError(f"{request.address} has no `outputs`: list the paths to keep from the image.")
    args = [arg for output in request.outputs for arg in ("--output", output)]
    args += [arg for pattern in request.exclude_patterns for arg in ("--exclude", pattern)]
    build = runner_member(ContainerBuildRequest, oci, union_membership)(
        request.bundle,
        (*_run_args(oci, request.command), "--dest", request.tar_name, "files", *args),
        request.description,
        output_files=(request.tar_name,),
    )
    return await container_build(**implicitly({build: ContainerBuildRequest}))


def rules():
    return [*collect_rules(), UnionRule(ContainerBuildRequest, PodmanBuildRequest)]
