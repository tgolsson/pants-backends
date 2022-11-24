"""

"""

from __future__ import annotations

from abc import ABCMeta
from dataclasses import dataclass
from typing import ClassVar, Generic, Type, TypeVar

from pants.engine.console import Console
from pants.engine.goal import Goal, GoalSubsystem, Outputting
from pants.engine.process import Process
from pants.engine.rules import Get, MultiGet, collect_rules, goal_rule
from pants.engine.target import (
    FieldSet,
    ImmutableValue,
    NoApplicableTargetsBehavior,
    TargetRootsToFieldSets,
    TargetRootsToFieldSetsRequest,
    Targets,
)
from pants.engine.unions import UnionMembership, UnionRule, union
from pants.util.frozendict import FrozenDict
from pants.version import PANTS_SEMVER, Version
from pants_backend_bitwarden.pants_ext.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretValue,
)
from typing_extensions import final


class DecryptSubsystem(Outputting, GoalSubsystem):
    name = "decrypt"
    help = "A decrypt goal for secrets."


class Decrypt(Goal):
    subsystem_cls = DecryptSubsystem


_F = TypeVar("_F", bound=FieldSet)


if PANTS_SEMVER >= Version("2.15.0.dev0"):
    from pants.engine.environment import EnvironmentName

    @union(in_scope_types=[EnvironmentName])
    @dataclass(frozen=True)
    class DecryptRequest(Generic[_F]):
        field_set: _F

else:

    @union
    @dataclass(frozen=True)
    class DecryptRequest(Generic[_F]):
        field_set: _F


@dataclass(frozen=True)
class DecryptResponse:
    secret: SecretValue


_T = TypeVar("_T", bound=DecryptRequest)


class DecryptOutputData(FrozenDict[str, ImmutableValue]):
    pass


if PANTS_SEMVER >= Version("2.15.0.dev0"):
    from pants.engine.environment import EnvironmentName

    @union(in_scope_types=[EnvironmentName])
    @dataclass(frozen=True)
    class DecryptFieldSet(Generic[_T], FieldSet, metaclass=ABCMeta):
        """FieldSet for DecryptRequest.
        Union members may list any fields required to fulfill the instantiation of the
        `DecryptProcesses` result of the decrypt rule.
        """

        # Subclasses must provide this, to a union member (subclass) of `DecryptRequest`.
        decrypt_request_type: ClassVar[Type[_T]]  # type: ignore[misc]

        @final
        def _request(self) -> _T:
            """Internal helper for the core decrypt goal."""
            return self.decrypt_request_type(field_set=self)

        @final
        @classmethod
        def rules(cls) -> tuple[UnionRule, ...]:
            """Helper method for registering the union members."""
            return (
                UnionRule(DecryptFieldSet, cls),
                UnionRule(DecryptRequest, cls.decrypt_request_type),
            )

        def get_output_data(self) -> DecryptOutputData:
            return DecryptOutputData({"target": self.address})

else:

    @union
    @dataclass(frozen=True)
    class DecryptFieldSet(Generic[_T], FieldSet, metaclass=ABCMeta):
        """FieldSet for DecryptRequest.
        Union members may list any fields required to fulfill the instantiation of the
        `DecryptProcesses` result of the decrypt rule.
        """

        # Subclasses must provide this, to a union member (subclass) of `DecryptRequest`.
        decrypt_request_type: ClassVar[Type[_T]]  # type: ignore[misc]

        @final
        def _request(self) -> _T:
            """Internal helper for the core decrypt goal."""
            return self.decrypt_request_type(field_set=self)

        @final
        @classmethod
        def rules(cls) -> tuple[UnionRule, ...]:
            """Helper method for registering the union members."""
            return (
                UnionRule(DecryptFieldSet, cls),
                UnionRule(DecryptRequest, cls.decrypt_request_type),
            )

        def get_output_data(self) -> DecryptOutputData:
            return DecryptOutputData({"target": self.address})


class DecryptProcess(Process):
    pass


@goal_rule
async def decrypt(
    console: Console,
    hello_world_subsystem: DecryptSubsystem,
    targets: Targets,
    unions: UnionMembership,
) -> Decrypt:
    target_roots_to_decrypt_field_sets = await Get(
        TargetRootsToFieldSets,
        TargetRootsToFieldSetsRequest(
            DecryptFieldSet,
            goal_description="Generate field sets",
            no_applicable_targets_behavior=NoApplicableTargetsBehavior.ignore,
        ),
    )

    gets = []
    for tgt in targets:
        if tgt not in target_roots_to_decrypt_field_sets.mapping:
            continue

        mapping = target_roots_to_decrypt_field_sets.mapping[tgt]

        get = Get(
            DecryptResponse,
            DecryptRequest,
            mapping[0]._request(),
        )

        gets.append(get)

    responses = await MultiGet(*gets)
    with hello_world_subsystem.output(console) as write_stdout:
        for response in responses:
            secret = response.secret
            write_stdout(f"Secret {secret.name} from {secret.source}: {secret.value}")

    return Decrypt(exit_code=0)


def rules():
    return collect_rules()
