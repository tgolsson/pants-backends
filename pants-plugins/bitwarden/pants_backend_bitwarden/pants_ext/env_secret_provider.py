"""

"""

import os
from dataclasses import dataclass

from pants.engine.environment import Environment, EnvironmentRequest
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import UnionRule
from pants_backend_bitwarden.pants_ext.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretsResponse,
    SecretValue,
)
from pants_backend_bitwarden.pants_ext.targets import EnvironmentSecretKey


@dataclass(frozen=True)
class EnvironmentSecretResponse(SecretsResponse):
    def cacheable(self) -> bool:
        return False


@dataclass(frozen=True)
class EnvironmentSecretFieldSet(FieldSet):
    required_fields = (EnvironmentSecretKey,)
    key: EnvironmentSecretKey


@dataclass(frozen=True)
class FallibleEnvironmentSecretsRequest(FallibleSecretsRequest):
    field_set_type = EnvironmentSecretFieldSet

    def cacheable(self) -> bool:
        return "CI" not in os.environ


@rule
async def get_environment_key(
    request: FallibleEnvironmentSecretsRequest,
) -> FallibleSecretsResponse:
    relevant_env = await Get(Environment, EnvironmentRequest([request.target.key.value]))

    if request.target.key.value not in relevant_env:
        return FallibleSecretsResponse(
            exit_code=1,
            stdout=f"secret is not in host environment: `{request.target.key.value}`",
        )

    # no stdout or stderr on exit 0, to avoid leaks
    return FallibleSecretsResponse(
        exit_code=0,
        response=SecretsResponse(
            SecretValue(
                relevant_env[request.target.key.value], request.target.address, "Environment"
            ),
        ),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(
            FallibleSecretsRequest,
            FallibleEnvironmentSecretsRequest,
        ),
    ]
