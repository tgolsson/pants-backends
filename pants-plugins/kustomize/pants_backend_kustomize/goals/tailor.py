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

from pants_backend_kustomize.subsystem import KustomizeTool
from pants_backend_kustomize.target_types import KustomizeTarget


@dataclass(frozen=True)
class PutativeKustomizeTargetsRequest(PutativeTargetsRequest):
    pass


@rule(level=LogLevel.DEBUG, desc="Determine candidate Kustomize targets to create")
async def find_putative_targets(
    req: PutativeKustomizeTargetsRequest,
    all_owned_sources: AllOwnedSources,
    kustomize: KustomizeTool,
) -> PutativeTargets:
    if not kustomize.tailor:
        return PutativeTargets()

    paths = await Get(Paths, PathGlobs, req.path_globs("kustomization.yaml"))

    unowned_files = set(paths.files) - set(all_owned_sources)

    pts = []

    for dirname, filenames in group_by_dir(unowned_files).items():
        pts.append(
            PutativeTarget.for_target_type(
                KustomizeTarget,
                path=dirname,
                name="kustomization",
                triggering_sources=sorted(filenames),
            )
        )

    return PutativeTargets(pts)


def rules():
    return [
        *collect_rules(),
        UnionRule(PutativeTargetsRequest, PutativeKustomizeTargetsRequest),
    ]
