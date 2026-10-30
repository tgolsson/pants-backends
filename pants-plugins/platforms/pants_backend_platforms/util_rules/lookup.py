"""The targets of one type in the repository, without `AllTargets`.

`AllTargets` generates every target, so a rule that awaits it cannot run during target
generation (it would wait on itself). Reading BUILD files and constructing only the targets of
one alias avoids that, and is cheaper. It matches the alias as written in BUILD files, so targets
of a subclass registered under another alias are not found.
"""

from __future__ import annotations

from dataclasses import dataclass

from pants.base.specs import RawSpecsWithoutFileOwners, RecursiveGlobSpec
from pants.engine.collection import Collection
from pants.engine.internals.graph import (
    address_families_from_raw_specs_without_file_owners,
    resolve_target_parametrizations,
)
from pants.engine.internals.parametrize import _TargetParametrizationsRequest
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import Target
from pants.util.logging import LogLevel


@dataclass(frozen=True)
class TargetsOfAliasRequest:
    alias: str


class TargetsOfAlias(Collection[Target]):
    pass


@rule(desc="Find targets of one type", level=LogLevel.DEBUG)
async def targets_of_alias(request: TargetsOfAliasRequest) -> TargetsOfAlias:
    families = await address_families_from_raw_specs_without_file_owners(
        RawSpecsWithoutFileOwners(
            recursive_globs=(RecursiveGlobSpec(""),),
            description_of_origin=f"the `{request.alias}` targets",
        ),
        **implicitly(),
    )
    # Private, but the only input `resolve_target_parametrizations` takes; Pants' own
    # `resolve_all_generator_target_requests` builds it the same way.
    parametrizations = await concurrently(
        resolve_target_parametrizations(
            _TargetParametrizationsRequest(address, f"the `{request.alias}` targets"), **implicitly()
        )
        for family in families
        for address, adaptor in family.addresses_to_target_adaptors.items()
        if adaptor.type_alias == request.alias
    )
    return TargetsOfAlias(t for p in parametrizations for t in p.all if not t.address.is_generated_target)


def rules():
    return collect_rules()
