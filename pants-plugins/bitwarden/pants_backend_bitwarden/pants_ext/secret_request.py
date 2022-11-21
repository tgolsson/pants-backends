from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.engine_aware import EngineAwareReturnType
from pants.engine.target import FieldSet, Target
from pants.engine.unions import union


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


@union
@dataclass(frozen=True)
class FallibleSecretsRequest:
    target: Target

    field_set: ClassVar[FieldSet]


@dataclass(frozen=True)
class SecretsResponse:
    value: SecretValue

    def __post_init__(self):
        assert isinstance(self.value, SecretValue)

    def cacheable(self) -> bool:
        return False


@dataclass(frozen=True)
class FallibleSecretsResponse(EngineAwareReturnType):
    exit_code: int
    response: SecretsResponse | None = None

    def cacheable(self) -> bool:
        if self.exit_code != 0:
            return True

        return self.response.cacheable()
