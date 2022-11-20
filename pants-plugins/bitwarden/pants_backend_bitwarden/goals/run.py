"""

"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.run import RunDebugAdapterRequest, RunFieldSet, RunRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import Target, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants_backend_bitwarden.subsystem import BitwardenTool
from pants_backend_bitwarden.targets import BitWardenId, BitWardenItemField
from pants_backend_oci.tools.process import FusedProcess


@dataclass(frozen=True)
class RunBitWardenCommand(RunFieldSet):
    required_fields = (BitWardenItemField,)

    item: BitWardenItemField


@dataclass(frozen=True)
class RunBitWardenProcessRequest:
    target: Target


@rule
async def prepare_run_image_bundle(
    request: RunBitWardenProcessRequest,
    tool: BitwardenTool,
    platform: Platform,
) -> Process:
    bw_tool = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

    item = await Get(
        Addresses,
        UnparsedAddressInputs,
        request.target[BitWardenItemField].to_unparsed_address_inputs(),
    )

    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(
            item[0],
            description_of_origin="Resolve BitWarden ID",
        ),
    )

    command = (f"{{chroot}}/{bw_tool.exe} get password {wrapped_target.target[BitWardenId].value}",)

    return await Get(
        Process,
        FusedProcess(
            (
                Process(
                    command,
                    description=f"Running {request.target}",
                    input_digest=bw_tool.digest,
                ),
            )
        ),
    )


@rule
async def run_oci_command_target(request: RunBitWardenCommand) -> RunRequest:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.address, description_of_origin="package_oci_image"),
    )
    process = await Get(Process, RunBitWardenProcessRequest(wrapped_target.target))

    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
    )


@rule
async def run_debug_oci_command_target(
    field_set: RunBitWardenCommand,
) -> RunDebugAdapterRequest:
    raise NotImplementedError("Cannot run kubernetes commands in debug mode.")


def rules():
    return [*collect_rules(), UnionRule(RunFieldSet, RunBitWardenCommand)]
