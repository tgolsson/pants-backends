from dataclasses import dataclass

from pants.engine.env_vars import EnvironmentVars as EnvironmentVars
from pants.engine.env_vars import EnvironmentVarsRequest as EnvironmentVarsRequest
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import UnionRule

from pants_backend_secrets.exception import MissingSecret
from pants_backend_secrets.goals.decrypt import DecryptFieldSet, DecryptRequest, DecryptResponse
from pants_backend_secrets.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretsResponse,
    SecretValue,
)
from pants_backend_secrets.targets import EnvironmentSecretKey


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


@rule
async def get_environment_key(
    request: FallibleEnvironmentSecretsRequest,
) -> FallibleSecretsResponse:
    relevant_env = await Get(EnvironmentVars, EnvironmentVarsRequest([request.target.key.value]))

    if request.target.key.value not in relevant_env:
        return FallibleSecretsResponse(
            exit_code=1,
            stdout=f"secret is not in host environment: `{request.target.key.value}`",
        )

    # no stdout or stderr on exit 0, to avoid leaks
    return FallibleSecretsResponse(
        exit_code=0,
        response=SecretsResponse(
            SecretValue(relevant_env[request.target.key.value], request.target.address, "EnvironmentVars"),
        ),
    )


class DecryptEnvironmentVarsRequest(DecryptRequest):
    pass


@dataclass(frozen=True)
class DecryptEnvironmentFieldSet(DecryptFieldSet):
    decrypt_request_type = DecryptEnvironmentVarsRequest
    required_fields = (EnvironmentSecretKey,)

    key: EnvironmentSecretKey


@rule
async def decrypt_environment(request: DecryptEnvironmentVarsRequest) -> DecryptResponse:
    response = await Get(FallibleSecretsResponse, FallibleEnvironmentSecretsRequest(request.field_set))

    if response.exit_code != 0:
        raise MissingSecret(f"failed retrieving environment secret: {response.stdout}")

    return DecryptResponse(response.response.value)


def rules():
    return [
        *collect_rules(),
        UnionRule(
            FallibleSecretsRequest,
            FallibleEnvironmentSecretsRequest,
        ),
        *DecryptEnvironmentFieldSet.rules(),
    ]
