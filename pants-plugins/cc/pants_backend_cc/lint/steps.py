"""The compile steps a per-translation-unit linter runs over: a target's steps for the host whose
source is a workspace file under the directories its paths field names."""

from __future__ import annotations

import os
from collections.abc import Iterable

from pants.engine.addresses import Address
from pants.engine.internals.graph import find_valid_field_sets, resolve_target
from pants.engine.rules import implicitly
from pants.engine.target import FieldSetsPerTargetRequest, WrappedTargetRequest

from pants_backend_cc.util_rules.compile import CompileStep, CompileStepsFieldSet, compile_steps
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform


def under(path: str, dirs: Iterable[str]) -> bool:
    return any(d == "." or path == d or path.startswith(f"{d}/") for d in dirs)


def build_root_dirs(address: Address, paths: Iterable[str]) -> list[str]:
    """`paths` are relative to the target's BUILD file."""
    return [os.path.normpath(os.path.join(address.spec_path, d)) for d in paths]


async def host_compile_steps(address: Address, field_alias: str, known: KnownPlatforms) -> list[CompileStep]:
    wrapped = await resolve_target(
        WrappedTargetRequest(address, description_of_origin=f"`{field_alias}`"), **implicitly()
    )
    per_target = await find_valid_field_sets(
        FieldSetsPerTargetRequest(CompileStepsFieldSet, [wrapped.target]), **implicitly()
    )
    providers = per_target.field_sets
    if len(providers) != 1:
        kinds = sorted(type(fs).__name__ for fs in providers)
        raise ValueError(
            f"{address}: `{field_alias}` needs exactly one compile-step provider for the target, "
            f"found {kinds}"
        )
    steps = await compile_steps(
        **implicitly({providers[0]: CompileStepsFieldSet, known.host: TargetPlatform})
    )
    return list(steps)


async def steps_under(
    address: Address, field_alias: str, paths: tuple[str, ...], known: KnownPlatforms
) -> list[CompileStep]:
    """The host steps whose non-generated source is under `paths`; none is an error, as the field
    then names nothing to lint."""
    dirs = build_root_dirs(address, paths)
    steps = [
        s
        for s in await host_compile_steps(address, field_alias, known)
        if not s.generated and under(s.source, dirs)
    ]
    if not steps:
        raise ValueError(f"{address}: no translation unit is under `{field_alias}` {list(paths)}")
    return steps
