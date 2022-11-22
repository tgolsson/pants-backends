"""

"""

import os

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.environment import Environment, EnvironmentRequest
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants_backend_bitwarden.pants_ext.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretsResponse,
    SecretValue,
)
from pants_backend_bitwarden.subsystem import BitwardenTool
from pants_backend_bitwarden.targets import (
    BitWardenFieldField,
    BitWardenId,
    BitWardenItem,
    BitWardenItemField,
)


class BitWardenSecretResponse(SecretsResponse):
    def cacheable(self) -> bool:
        return "CI" not in os.environ


class FallibleBitWardenSecretsRequest(SecretsResponse):
    def cacheable(self) -> bool:
        return "CI" not in os.environ


class BitWardenFieldSet(FieldSet):
    required_types = (BitWardenItem,)
    key: BitWardenItem


@rule
async def get_bitwarden_key(
    request: FallibleBitWardenSecretsRequest, tool: BitwardenTool, platform: Platform
) -> FallibleSecretsResponse:
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

    return FallibleSecretsResponse(
        exit_code=0,
        response=SecretsResponse(
            SecretValue(result.stdout.decode("utf-8"), request.field_set.address, "BitWarden"),
        ),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(
            FallibleSecretsRequest,
            FallibleBitWardenSecretsRequest,
        ),
    ]
