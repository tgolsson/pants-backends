"""
Requests that are intended for external users.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.addresses import Address
from pants.engine.environment import EnvironmentName
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionMembership, union
from pants.util.strutil import bullet_list


@dataclass(frozen=True)
class KustomizeInjectRequestQuery:
    """Query for whether the provided target can be the source of Kustomize injection data."""

    target: Target


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class KustomizeInjectRequest:
    """Base class for requests to generate data for Kustomize injection."""

    target: Target
    field_set_type: ClassVar[type[FieldSet]]


@dataclass(frozen=True)
class KustomizeInjectData:
    """Base class for requests to generate data for Kustomize injection."""

    address: Address
    value: str


@dataclass(frozen=True)
class KustomizeInjectRequestWrap:
    """Wrapper for a variant of KustomizeInjectRequest"""

    valid: bool
    request: KustomizeInjectRequest | None = None


@rule
def kirr_to_kir(
    request: KustomizeInjectRequestQuery, union_membership: UnionMembership
) -> KustomizeInjectRequestWrap:
    tgt = request.target
    concrete_requests = [
        request_type(request_type.field_set_type.create(tgt))
        for request_type in union_membership[KustomizeInjectRequest]
        if request_type.field_set_type.is_applicable(tgt)
    ]
    if len(concrete_requests) > 1:
        raise ValueError(
            f"Multiple registered builders from {KustomizeInjectRequest.__name__} can build target "
            f"{tgt.name}. It is ambiguous which implementation to use.\n\n"
            "Possible implementations:\n\n"
            f"{bullet_list(sorted(generator.__name__ for generator in concrete_requests))}"
        )

    if len(concrete_requests) == 0:
        return KustomizeInjectRequestWrap(False, None)

    first_concrete = concrete_requests[0]
    return KustomizeInjectRequestWrap(True, first_concrete)


def rules():
    return [
        *collect_rules(),
    ]
