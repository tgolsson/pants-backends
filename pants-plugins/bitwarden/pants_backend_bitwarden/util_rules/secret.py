""" """

import json
import os
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.env_vars import EnvironmentVars, EnvironmentVarsRequest
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process, ProcessCacheScope
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule

from pants_backend_bitwarden.subsystem import BitwardenTool
from pants_backend_bitwarden.targets import (
    BitWardenFieldField,
    BitWardenId,
    BitWardenItem,
    BitWardenItemField,
    BitWardenSessionSecret,
)
from pants_backend_secrets.exception import NoDecrypterException
from pants_backend_secrets.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretsRequestRequest,
    SecretsRequestWrap,
    SecretsResponse,
    SecretValue,
)


@dataclass(frozen=True)
class BitWardenSecretResponse(SecretsResponse):
    def cacheable(self) -> bool:
        return "CI" not in os.environ


@dataclass(frozen=True)
class BitWardenFieldSet(FieldSet):
    required_fields = (BitWardenItemField,)
    item: BitWardenItemField
    field: BitWardenFieldField


@dataclass(frozen=True)
class FallibleBitWardenSecretsRequest(FallibleSecretsRequest):
    field_set_type = BitWardenFieldSet


@dataclass(frozen=True)
class BitWardenSessionKeyRequest:
    item: BitWardenItem


class NoVaultKeyError(Exception):
    pass


@rule
async def get_bitwarden_session_secret(request: BitWardenSessionKeyRequest) -> SecretsResponse:
    secret = request.item[BitWardenSessionSecret].value
    secret_address = await Get(
        Addresses,
        UnparsedAddressInputs(
            [secret],
            owning_address=request.item.address,
            description_of_origin=f"the `{secret}` from the target {request.item}",
        ),
    )
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(secret_address[0], description_of_origin="twine_upload_with_secret"),
    )

    secret_request = await Get(SecretsRequestWrap, SecretsRequestRequest(wrapped_target.target))
    if secret_request.request is None:
        raise NoDecrypterException(
            f"No valid decrypter found for secret: `{secret_address[0]}` of type"
            f" `{wrapped_target.target.alias}`"
        )

    response = await Get(FallibleSecretsResponse, FallibleSecretsRequest, secret_request.request)
    if response.exit_code != 0:
        raise NoVaultKeyError(response.stdout)

    return response.response


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

    env_request = ["HOME"]
    extra_env = EnvironmentVars()
    if wrapped_target.target[BitWardenSessionSecret].value is not None:
        bw_session_secret = await Get(SecretsResponse, BitWardenSessionKeyRequest(wrapped_target.target))
        extra_env = EnvironmentVars(**{"BW_SESSION": bw_session_secret.value.value})
    else:
        env_request.append("BW_SESSION")

    relevant_env = await Get(EnvironmentVars, EnvironmentVarsRequest(env_request))
    command_env = EnvironmentVars(**relevant_env, **extra_env)

    if request.target.field.value:
        command = [
            f"{bw_tool.exe}",
            "--nointeraction",
            "get",
            "item",
            f"{wrapped_target.target[BitWardenId].value}",
        ]

        result: FallibleProcessResult = await Get(
            FallibleProcessResult,
            Process(
                command,
                description=f"Decrypting {request.target.address}",
                input_digest=bw_tool.digest,
                env=command_env,
                cache_scope=ProcessCacheScope.PER_SESSION,
            ),
        )

        if result.exit_code != 0:
            return FallibleSecretsResponse(
                exit_code=result.exit_code,
                stdout=result.stdout.decode("utf-8"),
                stderr=result.stderr.decode("utf-8"),
                response=None,
            )

        fields = json.loads(result.stdout.decode("utf-8"))["fields"]
        for field in fields:
            if field["name"] == request.target.field.value:
                return FallibleSecretsResponse(
                    exit_code=0,
                    response=SecretsResponse(
                        SecretValue(field["value"], request.target.address, "BitWarden"),
                    ),
                )
                break

        return FallibleSecretsResponse(
            exit_code=1,
            stdout="",
            stderr=(
                f"no field `{request.target.field.field_name}` for id"
                f" `{wrapped_target.target[BitWardenId].value}`"
            ),
            response=None,
        )

    command = [
        f"{bw_tool.exe}",
        "--nointeraction",
        "get",
        "password",
        f"{wrapped_target.target[BitWardenId].value}",
    ]

    result: FallibleProcessResult = await Get(
        FallibleProcessResult,
        Process(
            command,
            description=f"Decrypting {request.target.address}",
            input_digest=bw_tool.digest,
            env=command_env,
            cache_scope=ProcessCacheScope.PER_SESSION,
        ),
    )

    if result.exit_code != 0:
        return FallibleSecretsResponse(
            exit_code=result.exit_code,
            stdout=result.stdout.decode("utf-8"),
            stderr=result.stderr.decode("utf-8"),
            response=None,
        )

    # NB[TSolberg]: no stdout or stderr on exit 0, to avoid leaks
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
