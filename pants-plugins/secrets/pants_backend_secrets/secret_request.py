from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.engine_aware import EngineAwareReturnType
from pants.engine.environment import EnvironmentName
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionMembership, union
from pants.util.strutil import bullet_list

from pants_backend_secrets.exception import FailedDecryptException


@dataclass(frozen=True)
class SecretValue:
    """Wrapped for a secret which redacts its value when printed"""

    value: str
    """The value of the secret"""

    name: str | None = None
    """The name of the secret. Used in lieu of the secret value in logs."""

    source: str | None = None
    """The source of the secret - such as the tool or system owning it. Only used in logs."""

    def __repr__(self):
        return f"Secret(<redacted>, {repr(self.name)}, {repr(self.source)})"

    def __str__(self):
        msg = "Secret"
        if self.name:
            msg += f" `{self.name}`"

        if self.source:
            msg += f" from `{self.source}`"

        return f"<{msg}>"


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class FallibleSecretsRequest:
    target: Target

    field_set_type: ClassVar[FieldSet]


@dataclass(frozen=True)
class SecretsRequestRequest:
    target: Target


@dataclass(frozen=True)
class SecretsRequestWrap:
    request: FallibleSecretsRequest | None


@dataclass(frozen=True)
class SecretsResponse:
    value: SecretValue

    def __post_init__(self):
        assert isinstance(self.value, SecretValue), f"value must be SecretValue but was {type(self.value)}"

    def cacheable(self) -> bool:
        return False


@dataclass(frozen=True)
class FallibleSecretsResponse(EngineAwareReturnType):
    exit_code: int
    stdout: str | None = None
    stderr: str | None = None
    response: SecretsResponse | None = None

    def cacheable(self) -> bool:
        if self.exit_code != 0:
            return True

        return self.response.cacheable()


@rule
def secrets_request_request(
    request: SecretsRequestRequest, union_membership: UnionMembership
) -> SecretsRequestWrap:
    tgt = request.target

    concrete_requests = [
        request_type(request_type.field_set_type.create(tgt))
        for request_type in union_membership[FallibleSecretsRequest]
        if request_type.field_set_type.is_applicable(tgt)
    ]

    if len(concrete_requests) != 1:
        raise ValueError(
            f"Multiple or zero registered decrypters from {SecretsRequestRequest.__name__} can "
            f"build target {tgt.name}. It is ambiguous which implementation to use.\n\n"
            "Possible implementations:\n\n"
            f"{bullet_list(sorted(generator.__name__ for generator in concrete_requests))}"
        )

    if len(concrete_requests) == 0:
        return SecretsRequestWrap(None)

    first_concrete = concrete_requests[0]
    return SecretsRequestWrap(first_concrete)


@rule
def unwrap_fallible_secret(fallible: FallibleSecretsResponse) -> SecretsResponse:
    if fallible.exit_code != 0:
        raise FailedDecryptException(
            "Failed decrypting secret",
            fallible.stdout,
            fallible.stderr,
        )

    return fallible.response


def rules():
    return [
        *collect_rules(),
    ]
