"""

"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.run import RunDebugAdapterRequest, RunFieldSet, RunRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import MkdirBinary
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import Target, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants_backend_oci.subsystem import RuncTool
from pants_backend_oci.targets import ImageRepository
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundleRequest,
)
from pants_backend_oci.util_rules.unpack import UnpackedImageBundleRequest


@dataclass(frozen=True)
class RunImageBundleCommand(RunFieldSet):
    required_fields = (ImageRepository,)

    repository: ImageRepository


@dataclass(frozen=True)
class RunImageBundleProcessRequest:
    target: Target


@rule
async def prepare_run_image_bundle(
    request: RunImageBundleProcessRequest,
    tool: RuncTool,
    mkdir_binary: MkdirBinary,
    platform: Platform,
) -> Process:
    download_runc_tool = Get(
        DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform)
    )
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.target.address, description_of_origin="package_oci_image"),
    )
    target = wrapped_target.target
    bundle_request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(target))
    image = Get(FallibleImageBundle, FallibleImageBundleRequest, bundle_request.request)

    tool, image = await MultiGet(download_runc_tool, image)
    if image.exit_code != 0:
        raise ValueError(image.stderr)

    mkdir_process = Process(
        (mkdir_binary.path, "{chroot}/runspace"),
        description="Creating runspace directory for runc",
    )

    packed_image_process = await Get(Process, UnpackedImageBundleRequest(image.output.digest))

    name = str(request.target.address).replace("/", "_").replace(":", "_").replace("#", "_")
    command = (f"{{chroot}}/{tool.exe} --root runspace run -b unpacked_image pants.runc.{name}",)

    return await Get(
        Process,
        FusedProcess(
            (
                packed_image_process,
                mkdir_process,
                Process(
                    command,
                    description=f"Running {request.target}",
                    input_digest=tool.digest,
                ),
            )
        ),
    )


@rule
async def run_oci_command_target(request: RunImageBundleCommand) -> RunRequest:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.address, description_of_origin="package_oci_image"),
    )
    process = await Get(Process, RunImageBundleProcessRequest(wrapped_target.target))

    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
    )


@rule
async def run_debug_oci_command_target(
    field_set: RunImageBundleCommand,
) -> RunDebugAdapterRequest:
    raise NotImplementedError("Cannot run kubernetes commands in debug mode.")


def rules():
    return [*collect_rules(), UnionRule(RunFieldSet, RunImageBundleCommand)]
