"""`pants tailor`: a `cargo_package` for every unowned `Cargo.toml` with a `[package]`, a
`cargo_workspace` for every one with a `[workspace]`."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.goals.tailor import (
    AllOwnedSources,
    PutativeTarget,
    PutativeTargets,
    PutativeTargetsRequest,
)
from pants.engine.intrinsics import get_digest_contents, path_globs_to_digest
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule
from pants.util.dirutil import group_by_dir
from pants.util.logging import LogLevel
from pants_backend_cargo.subsystems import RustSubsystem
from pants_backend_cargo.target_types import CargoPackageTarget, CargoWorkspaceTarget


@dataclass(frozen=True)
class PutativeCargoTargetsRequest(PutativeTargetsRequest):
    pass


@rule(level=LogLevel.DEBUG, desc="Determine candidate cargo targets to create")
async def find_putative_targets(
    request: PutativeCargoTargetsRequest, all_owned_sources: AllOwnedSources, rust: RustSubsystem
) -> PutativeTargets:
    if not rust.tailor:
        return PutativeTargets()

    contents = await get_digest_contents(await path_globs_to_digest(request.path_globs("Cargo.toml")))
    unowned = {fc.path: fc.content for fc in contents if fc.path not in all_owned_sources}

    putative = []
    for dirname, filenames in group_by_dir(unowned).items():
        manifest = unowned[os.path.join(dirname, "Cargo.toml")]
        if b"[package]" in manifest:
            putative.append(
                PutativeTarget.for_target_type(
                    CargoPackageTarget,
                    path=dirname,
                    name=None,
                    triggering_sources=sorted(filenames),
                )
            )
        if b"[workspace]" in manifest:
            putative.append(
                PutativeTarget.for_target_type(
                    CargoWorkspaceTarget,
                    path=dirname,
                    name="workspace",
                    triggering_sources=sorted(filenames),
                )
            )
    return PutativeTargets(putative)


def rules():
    return [*collect_rules(), UnionRule(PutativeTargetsRequest, PutativeCargoTargetsRequest)]
