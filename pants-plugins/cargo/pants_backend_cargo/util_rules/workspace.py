"""Which packages belong to which workspace, from the workspace manifests' `members`."""

from __future__ import annotations

import fnmatch
import os
import tomllib
from dataclasses import dataclass

from pants.build_graph.address import Address
from pants.engine.fs import DigestSubset, PathGlobs
from pants.engine.internals.graph import hydrate_sources
from pants.engine.intrinsics import digest_subset_to_digest, get_digest_contents
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import AllTargets, HydrateSourcesRequest, MultipleSourcesField
from pants.util.frozendict import FrozenDict
from pants_backend_cargo.target_types import (
    CargoPackageTargetImpl,
    CargoWorkspaceSourcesField,
    CargoWorkspaceTarget,
    _CargoPackageMarker,
)
from pants_backend_cargo.util_rules.cargo import manifest_path


@dataclass(frozen=True)
class AllCargoTargets:
    packages: tuple[CargoPackageTargetImpl, ...]
    workspaces: tuple[CargoWorkspaceTarget, ...]


@rule(desc="Find all cargo packages and workspaces")
async def find_all_cargo_targets(all_targets: AllTargets) -> AllCargoTargets:
    packages = []
    workspaces = []
    for target in all_targets:
        if target.has_field(CargoWorkspaceSourcesField):
            workspaces.append(target)
        if target.has_field(_CargoPackageMarker):
            packages.append(target)
    return AllCargoTargets(packages=tuple(packages), workspaces=tuple(workspaces))


@dataclass(frozen=True)
class CargoToml:
    contents: str


@dataclass(frozen=True)
class CargoTomlRequest:
    sources: MultipleSourcesField


@rule(desc="Load a Cargo.toml")
async def load_cargo_toml(request: CargoTomlRequest) -> CargoToml:
    hydrated = await hydrate_sources(HydrateSourcesRequest(request.sources), **implicitly())
    # Exactly the target's own: `sources` may hold member manifests too.
    manifest = await digest_subset_to_digest(
        DigestSubset(
            hydrated.snapshot.digest,
            PathGlobs([manifest_path(request.sources.address.spec_path)]),
        )
    )
    contents = await get_digest_contents(manifest)
    if not contents:
        raise ValueError(f"{request.sources.address}: no Cargo.toml among its sources")
    return CargoToml(contents[0].content.decode())


@dataclass(frozen=True)
class CargoWorkspaceMember:
    member_path: str
    package: CargoPackageTargetImpl


@dataclass(frozen=True)
class CargoPackageMapping:
    workspace_to_packages: FrozenDict[Address, frozenset[CargoWorkspaceMember]]
    loose_packages: frozenset[CargoPackageTargetImpl]

    def workspace_for(self, address: Address) -> Address | None:
        """The workspace a package (by its `cargo_package_impl` address) belongs to."""
        for workspace, members in self.workspace_to_packages.items():
            if any(address == m.package.address for m in members):
                return workspace
        return None


def _matches(relative: str, pattern: str) -> bool:
    """`pattern` matches as cargo expands `members`: `*` within one path segment, `**` any
    number of segments, none included."""
    return _segments_match(relative.split("/"), os.path.normpath(pattern).split("/"))


def _segments_match(parts: list[str], wanted: list[str]) -> bool:
    if not wanted:
        return not parts
    if wanted[0] == "**":
        return any(_segments_match(parts[i:], wanted[1:]) for i in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], wanted[0]) and _segments_match(parts[1:], wanted[1:])


@rule(desc="Assign packages to workspaces")
async def assign_packages_to_workspaces(all_cargo_targets: AllCargoTargets) -> CargoPackageMapping:
    manifests = await concurrently(
        load_cargo_toml(CargoTomlRequest(workspace[CargoWorkspaceSourcesField]))
        for workspace in all_cargo_targets.workspaces
    )

    by_dir: dict[str, list[CargoPackageTargetImpl]] = {}
    for package in all_cargo_targets.packages:
        by_dir.setdefault(package.address.spec_path, []).append(package)

    workspace_to_packages: dict[Address, frozenset[CargoWorkspaceMember]] = {}
    packages = set(all_cargo_targets.packages)
    for workspace, manifest in zip(all_cargo_targets.workspaces, manifests):
        data = tomllib.loads(manifest.contents)
        table = data.get("workspace", {})
        members = [*table.get("members", []), *(["."] if "package" in data else [])]
        exclude = table.get("exclude", [])
        root = workspace.address.spec_path

        found = []
        for directory, package_targets in sorted(by_dir.items()):
            relative = os.path.relpath(directory or ".", root or ".")
            if relative.startswith("..") or not any(_matches(relative, m) for m in members):
                continue
            if any(_matches(relative, e) for e in exclude):
                continue
            if len(package_targets) > 1:
                raise ValueError(
                    f"{workspace.address}: member `{relative}` has more than one package target: "
                    f"{sorted(t.address for t in package_targets)}"
                )
            packages -= set(package_targets)
            found.append(CargoWorkspaceMember(member_path=relative, package=package_targets[0]))
        workspace_to_packages[workspace.address] = frozenset(found)

    return CargoPackageMapping(
        workspace_to_packages=FrozenDict(workspace_to_packages),
        loose_packages=frozenset(packages),
    )


def rules():
    return collect_rules()
