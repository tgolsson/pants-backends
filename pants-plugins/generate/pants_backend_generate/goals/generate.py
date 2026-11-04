"""`pants generate` writes the codegen outputs into the workspace: `fix` cannot create files and
`export-codegen` writes only under `dist/`."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.console import Console
from pants.engine.fs import MergeDigests, Snapshot, Workspace
from pants.engine.goal import Goal, GoalSubsystem
from pants.engine.internals.graph import hydrate_sources
from pants.engine.internals.specs_rules import find_valid_field_sets_for_target_roots
from pants.engine.intrinsics import merge_digests
from pants.engine.rules import collect_rules, concurrently, goal_rule, implicitly
from pants.engine.target import (
    HydrateSourcesRequest,
    NoApplicableTargetsBehavior,
    TargetRootsToFieldSetsRequest,
)
from pants.engine.unions import UnionMembership
from pants.option.option_types import SkipOption
from pants.util.strutil import bullet_list

from pants_backend_generate.target_types import GenerateFieldSet


class GenerateSubsystem(GoalSubsystem):
    name = "generate"
    help = "Run the generators of `*_generate` targets and write their outputs into the workspace."

    # Also the linter's tool subsystem: two subsystems cannot share the `generate` scope.
    skip = SkipOption("lint")

    @classmethod
    def activated(cls, union_membership: UnionMembership) -> bool:
        return GenerateFieldSet in union_membership


class Generate(Goal):
    subsystem_cls = GenerateSubsystem
    environment_behavior = Goal.EnvironmentBehavior.LOCAL_ONLY


@dataclass(frozen=True)
class GeneratedOutputs:
    address: Address
    snapshot: Snapshot


async def generate_outputs(field_set: GenerateFieldSet) -> GeneratedOutputs:
    hydrated = await hydrate_sources(
        HydrateSourcesRequest(
            field_set.sources,
            for_sources_types=(field_set.output_type,),
            enable_codegen=True,
        ),
        **implicitly(),
    )
    return GeneratedOutputs(field_set.address, hydrated.snapshot)


@goal_rule
async def generate(console: Console, workspace: Workspace) -> Generate:
    targets_to_field_sets = await find_valid_field_sets_for_target_roots(
        TargetRootsToFieldSetsRequest(
            GenerateFieldSet,
            goal_description=f"the `{GenerateSubsystem.name}` goal",
            no_applicable_targets_behavior=NoApplicableTargetsBehavior.warn,
        ),
        **implicitly(),
    )
    results = await concurrently(
        generate_outputs(field_set) for field_set in targets_to_field_sets.field_sets
    )
    results = sorted(results, key=lambda r: r.address)

    writers: defaultdict[str, list[Address]] = defaultdict(list)
    for result in results:
        for path in result.snapshot.files:
            writers[path].append(result.address)
    clashes = [
        f"{path}: {', '.join(str(a) for a in addresses)}"
        for path, addresses in sorted(writers.items())
        if len(addresses) > 1
    ]
    if clashes:
        console.print_stderr(
            f"More than one target writes the same file; nothing was written:\n\n{bullet_list(clashes)}"
        )
        return Generate(exit_code=1)

    digest = await merge_digests(MergeDigests(result.snapshot.digest for result in results))
    workspace.write_digest(digest)
    for result in results:
        console.print_stdout(f"{result.address}: wrote {', '.join(result.snapshot.files)}")
    return Generate(exit_code=0)


def rules():
    return collect_rules()
