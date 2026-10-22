"""Running scripted build steps, and finding the podman that runs them and `pants run`.

`podman_builder` does the work in a sandboxed process; these rules only decide what it builds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.core.util_rules.system_binaries import (
    BinaryPath,
    BinaryPathRequest,
    SystemBinariesSubsystem,
    find_binary,
)
from pants.engine.addresses import Address
from pants.engine.fs import MergeDigests
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciSubsystem
from pants_backend_oci.util_rules import oci_layout_builder, podman_builder, podman_runner
from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.layer import ImageLayer
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules

_LINUX_ONLY = "OCI containers require local Linux podman; podman machine is not supported yet."

# Shared with `pants run`, which reaches the same named cache by its absolute path.
PODMAN_CACHE = "oci_podman"
PODMAN_STORE_VERSION = "v1"

_SCRIPTED_LAYER = "scripted-layer.tar"


class PodmanBinary(BinaryPath):
    pass


@rule(desc="Finding podman for OCI containers")
async def find_podman(
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
    platform: Platform,
) -> PodmanBinary:
    if platform not in (Platform.linux_x86_64, Platform.linux_arm64):
        raise ValueError(_LINUX_ONLY)
    # Do not execute podman during discovery: even --version can initialize its host runtime.
    request = BinaryPathRequest(binary_name="podman", search_path=system_binaries.system_binary_paths)
    paths = await find_binary(request, **implicitly())
    binary = paths.first_path_or_raise(
        request,
        rationale="run OCI images. Install podman: https://podman.io/docs/installation",
    )
    return PodmanBinary(binary.path, binary.fingerprint)


@dataclass(frozen=True)
class PodmanBuildRequest:
    """Run `podman_builder` with `args` on `bundle`'s layout."""

    bundle: ImageBundle
    args: tuple[str, ...]
    description: str
    output_files: tuple[str, ...] = ()
    output_directories: tuple[str, ...] = ()


@rule
async def podman_build(
    request: PodmanBuildRequest,
    podman: PodmanBinary,
    python: PythonBuildStandaloneBinary,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> ProcessResult:
    builder = await create_digest(sandboxed_modules(podman_builder, podman_runner, oci_layout_builder))
    input_digest = await merge_digests(MergeDigests([request.bundle.digest, builder]))
    process = Process(
        argv=(
            python.path,
            "-c",
            module_main(podman_builder),
            "--podman",
            podman.path,
            "--cache",
            f".{PODMAN_CACHE}/{PODMAN_STORE_VERSION}",
            *request.args,
        ),
        input_digest=input_digest,
        append_only_caches={PODMAN_CACHE: f".{PODMAN_CACHE}"},
        # Podman finds newuidmap, conmon and its OCI runtime on the PATH.
        env={"PATH": ":".join(system_binaries.system_binary_paths)},
        output_files=request.output_files,
        output_directories=request.output_directories,
        description=request.description,
        level=LogLevel.DEBUG,
    )
    return await fallible_to_exec_result_or_raise(**implicitly({process: Process}))


def _run_args(oci: OciSubsystem, command: str | None) -> tuple[str, ...]:
    return ("--run", json.dumps([*oci.command_shell, command])) if command else ()


@dataclass(frozen=True)
class ScriptedLayerRequest:
    """The layer `command` adds when run in `bundle`, for the rules to append to it."""

    bundle: ImageBundle
    command: str
    address: Address


@rule(desc="Run scripted OCI build step", level=LogLevel.DEBUG)
async def run_scripted_layer(request: ScriptedLayerRequest, oci: OciSubsystem) -> ImageLayer:
    result = await podman_build(
        PodmanBuildRequest(
            request.bundle,
            (*_run_args(oci, request.command), "--dest", _SCRIPTED_LAYER, "layer"),
            f"Run build step for {request.address}",
            output_files=(_SCRIPTED_LAYER,),
        ),
        **implicitly(),
    )
    return ImageLayer(
        request.address, result.output_digest, _SCRIPTED_LAYER, created_by=f"Build step: {request.command}"
    )


@dataclass(frozen=True)
class ImageFilesRequest:
    """`outputs` from `bundle`, after running `command` if given, as the tar `tar_name`."""

    address: Address
    tar_name: str
    bundle: ImageBundle
    command: str | None
    outputs: tuple[str, ...]
    exclude_patterns: tuple[str, ...] = ()
    description: str = "Copy files out of OCI image"


@rule(desc="Copy files out of OCI image", level=LogLevel.DEBUG)
async def image_files(request: ImageFilesRequest, oci: OciSubsystem) -> ProcessResult:
    if not request.outputs:
        raise ValueError(f"{request.address} has no `outputs`: list the paths to keep from the image.")
    args = [arg for output in request.outputs for arg in ("--output", output)]
    args += [arg for pattern in request.exclude_patterns for arg in ("--exclude", pattern)]
    return await podman_build(
        PodmanBuildRequest(
            request.bundle,
            (*_run_args(oci, request.command), "--dest", request.tar_name, "files", *args),
            request.description,
            output_files=(request.tar_name,),
        ),
        **implicitly(),
    )


def rules():
    return collect_rules()
