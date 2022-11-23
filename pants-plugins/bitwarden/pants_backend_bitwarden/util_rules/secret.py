"""

"""

import os
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.environment import Environment, EnvironmentRequest
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
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


@dataclass(frozen=True)
class BitWardenSecretResponse(SecretsResponse):
    def cacheable(self) -> bool:
        return "CI" not in os.environ


@dataclass(frozen=True)
class BitWardenFieldSet(FieldSet):
    required_fields = (BitWardenItemField,)
    item: BitWardenItemField


@dataclass(frozen=True)
class FallibleBitWardenSecretsRequest(FallibleSecretsRequest):
    field_set_type = BitWardenFieldSet

    def cacheable(self) -> bool:
        return "CI" not in os.environ


@rule
async def get_bitwarden_key(
    request: FallibleBitWardenSecretsRequest, tool: BitwardenTool, platform: Platform
) -> FallibleSecretsResponse:
    bw_tool = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

    item = await Get(
        Addresses,
        UnparsedAddressInputs,
        request.target.item.to_unparsed_address_inputs(),
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
        "--nointeraction",
        "get",
        "password",
        f"{wrapped_target.target[BitWardenId].value}",
    )

    relevant_env = await Get(Environment, EnvironmentRequest(["HOME", "BW_SESSION"]))
    result: FallibleProcessResult = await Get(
        FallibleProcessResult,
        Process(
            command,
            description=f"Decrypting {request.target.item}",
            input_digest=bw_tool.digest,
            env=relevant_env,
        ),
    )

    if result.exit_code != 0:
        return FallibleSecretsResponse(
            exit_code=result.exit_code,
            stdout=result.stdout.decode("utf-8"),
            stderr=result.stderr.decode("utf-8"),
            response=None,
        )

    # no stdout or stderr on exit 0, to avoid leaks
    return FallibleSecretsResponse(
        exit_code=0,
        response=SecretsResponse(
            SecretValue(result.stdout.decode("utf-8"), request.target.address, "BitWarden"),
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
