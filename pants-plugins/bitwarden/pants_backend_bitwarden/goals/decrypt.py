"""

"""
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.environment import Environment, EnvironmentRequest
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import WrappedTarget, WrappedTargetRequest
from pants_backend_bitwarden.pants_ext.goals.decrypt import (
    DecryptFieldSet,
    DecryptRequest,
    DecryptResponse,
)
from pants_backend_bitwarden.pants_ext.secret_request import SecretValue
from pants_backend_bitwarden.subsystem import BitwardenTool
from pants_backend_bitwarden.targets import BitWardenFieldField, BitWardenId, BitWardenItemField


class DecryptBitwardenRequest(DecryptRequest):
    pass


@dataclass(frozen=True)
class DecryptBitwardenFieldSet(DecryptFieldSet):
    decrypt_request_type = DecryptBitwardenRequest
    required_fields = (BitWardenItemField,)

    item: BitWardenItemField
    field: BitWardenFieldField


@rule
async def decrypt_bitwarden(
    request: DecryptBitwardenRequest, tool: BitwardenTool, platform: Platform
) -> DecryptResponse:
    bw_tool = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

    item = await Get(
        Addresses,
        UnparsedAddressInputs,
        request.field_set.item.to_unparsed_address_inputs(),
    )

    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(
            item[0],
            description_of_origin="Resolve BitWarden ID",
        ),
    )

    command = (
        f"{bw_tool.exe}",
        "get",
        "password",
        f"{wrapped_target.target[BitWardenId].value}",
    )

    relevant_env = await Get(Environment, EnvironmentRequest(["HOME", "BW_SESSION"]))
    result = await Get(
        ProcessResult,
        Process(
            command,
            description=f"Decrypting {request.field_set.item}",
            input_digest=bw_tool.digest,
            env=relevant_env,
        ),
    )
    return DecryptResponse(
        SecretValue(result.stdout.decode("utf-8"), request.field_set.address, "BitWarden")
    )


def rules():
    return (
        *collect_rules(),
        *DecryptBitwardenFieldSet.rules(),
    )
