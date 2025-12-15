""" """

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.internals.graph import resolve_target
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import WrappedTargetRequest

from pants_backend_bitwarden.subsystem import BitwardenTool
from pants_backend_bitwarden.targets import BitWardenItemField
from pants_backend_bitwarden.util_rules.secret import FallibleBitWardenSecretsRequest
from pants_backend_secrets.goals.decrypt import DecryptFieldSet, DecryptRequest, DecryptResponse
from pants_backend_secrets.secret_request import unwrap_fallible_secret


class DecryptBitwardenRequest(DecryptRequest):
    pass


@dataclass(frozen=True)
class DecryptBitwardenFieldSet(DecryptFieldSet):
    decrypt_request_type = DecryptBitwardenRequest
    required_fields = (BitWardenItemField,)

    item: BitWardenItemField


@rule
async def decrypt_bitwarden(
    request: DecryptBitwardenRequest, tool: BitwardenTool, platform: Platform
) -> DecryptResponse:
    wrapped_target = await resolve_target(
        WrappedTargetRequest(
            request.field_set.address,
            description_of_origin="Resolve BitWarden ID",
        ),
        **implicitly(),
    )

    secret_response = await unwrap_fallible_secret(
        FallibleBitWardenSecretsRequest(
            FallibleBitWardenSecretsRequest.field_set_type.create(wrapped_target.target),
        ),
        **implicitly(),
    )

    return DecryptResponse(secret_response.value)


def rules():
    return (
        *collect_rules(),
        *DecryptBitwardenFieldSet.rules(),
    )
