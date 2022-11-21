"""

"""

import os

from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import UnionRule
from pants_backend_bitwarden.pants_ext.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretsResponse,
    SecretValue,
)
from pants_backend_bitwarden.subsystem import BitwardenTool
from pants_backend_bitwarden.targets import BitWardenItem


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
async def get_kms_key(
    request: FallibleBitWardenSecretsRequest, kms_tool: BitwardenTool
) -> FallibleSecretsResponse:
    pass


def rules():
    return [
        *collect_rules(),
        UnionRule(
            FallibleSecretsRequest,
            FallibleBitWardenSecretsRequest,
        ),
    ]
