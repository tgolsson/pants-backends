"""`pants generate-lockfiles`: `cargo update` per workspace and per package outside any
workspace. The resolve names are the addresses of workspaces and packages; a member package's
name updates its workspace's lockfile, since that is the only lockfile cargo keeps for it."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.goals.generate_lockfiles import (
    GenerateLockfile,
    GenerateLockfileResult,
    KnownUserResolveNames,
    KnownUserResolveNamesRequest,
    RequestedUserResolveNames,
    UserGenerateLockfiles,
)
from pants.engine.addresses import Address
from pants.engine.process import ProcessCacheScope, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule
from pants_backend_cargo.util_rules.cargo import CargoProcessRequest, manifest_path
from pants_backend_cargo.util_rules.rustup import install_rust_toolchain
from pants_backend_cargo.util_rules.sandbox import CargoSourcesRequest, cargo_sources
from pants_backend_cargo.util_rules.workspace import AllCargoTargets, CargoPackageMapping

from pants_backend_platforms.util_rules.platforms import KnownPlatforms


class RequestedCargoUserResolveNames(RequestedUserResolveNames):
    pass


class KnownCargoUserResolveNamesRequest(KnownUserResolveNamesRequest):
    pass


@rule
async def determine_cargo_user_resolves(
    _: KnownCargoUserResolveNamesRequest, all_targets: AllCargoTargets
) -> KnownUserResolveNames:
    # No environment is in scope here, so the workspace mapping (which reads manifests)
    # cannot be computed; every workspace and package is a name, mapped below.
    names = sorted(str(t.address) for t in (*all_targets.workspaces, *all_targets.packages))
    return KnownUserResolveNames(
        names=tuple(names),
        option_name="",
        requested_resolve_names_cls=RequestedCargoUserResolveNames,
    )


@dataclass(frozen=True)
class GenerateCargoLockfileRequest(GenerateLockfile):
    root: Address


@rule(desc="Generate a Cargo.lock")
async def generate_cargo_lockfile(
    request: GenerateCargoLockfileRequest, known: KnownPlatforms
) -> GenerateLockfileResult:
    toolchain = await install_rust_toolchain(**implicitly())
    sources = await cargo_sources(
        CargoSourcesRequest(
            frozenset([request.root]),
            include_packages=False,
            platform=known.host,
            dependent=request.root,
        )
    )
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            CargoProcessRequest(
                toolchain,
                ("update", f"--manifest-path={manifest_path(request.root.spec_path)}"),
                description=f"cargo update {request.root}",
                digest=sources.digest,
                env=sources.env,
                output_files=(request.lockfile_dest,),
                # The registry moves on: a cached `cargo update` would return an old lockfile.
                cache_scope=ProcessCacheScope.PER_SESSION,
            )
        )
    )
    return GenerateLockfileResult(result.output_digest, request.resolve_name, request.lockfile_dest)


@rule
async def setup_cargo_lockfile_requests(
    requested: RequestedCargoUserResolveNames,
    all_targets: AllCargoTargets,
    mapping: CargoPackageMapping,
) -> UserGenerateLockfiles:
    by_name = {str(t.address): t.address for t in (*all_targets.workspaces, *all_targets.packages)}
    roots: dict[Address, str] = {}
    for name in requested:
        address = by_name.get(name)
        if address is None:
            raise ValueError(f"`{name}` is not a cargo workspace or package address")
        root = mapping.workspace_for(address) or address
        roots.setdefault(root, str(root))
    return UserGenerateLockfiles(
        GenerateCargoLockfileRequest(
            root=root,
            resolve_name=name,
            lockfile_dest=os.path.join(root.spec_path, "Cargo.lock"),
            diff=False,
        )
        for root, name in roots.items()
    )


def rules():
    return (
        *collect_rules(),
        UnionRule(KnownUserResolveNamesRequest, KnownCargoUserResolveNamesRequest),
        UnionRule(RequestedUserResolveNames, RequestedCargoUserResolveNames),
        UnionRule(GenerateLockfile, GenerateCargoLockfileRequest),
    )
