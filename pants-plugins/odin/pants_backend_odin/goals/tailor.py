from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.tailor import (
    AllOwnedSources,
    PutativeTarget,
    PutativeTargets,
    PutativeTargetsRequest,
)
from pants.engine.fs import PathGlobs, Paths
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule
from pants.util.dirutil import group_by_dir
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinPackageTarget, OdinSourcesGeneratorTarget


@dataclass(frozen=True)
class PutativeOdinTargetsRequest(PutativeTargetsRequest):
    pass


@rule(level=LogLevel.DEBUG, desc="Determine candidate Odin targets to create")
async def find_putative_targets(
    req: PutativeOdinTargetsRequest,
    all_owned_sources: AllOwnedSources,
    odin: OdinTool,
) -> PutativeTargets:
    if not odin.tailor:
        return PutativeTargets()

    paths = await Get(Paths, PathGlobs, req.path_globs("*.odin"))
    unowned_files = set(paths.files) - set(all_owned_sources)

    pts = []

    for dirname, filenames in group_by_dir(unowned_files).items():
        # Generate odin_package target as the preferred choice
        pts.append(
            PutativeTarget.for_target_type(
                OdinPackageTarget,
                path=dirname,
                name="odin",
                triggering_sources=sorted(filenames),
            )
        )

    return PutativeTargets(pts)


def rules():
    return [
        *collect_rules(),
        UnionRule(PutativeTargetsRequest, PutativeOdinTargetsRequest),
    ]
