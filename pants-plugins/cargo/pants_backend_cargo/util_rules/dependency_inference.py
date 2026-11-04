"""Inferred dependencies: a package depends on its workspace (its manifest, lockfile and config)
and on the packages its `path` dependencies name, platform-specific ones included. A workspace
does not depend on its members (that would be a cycle); the sandbox rule adds them when a
workspace is the root."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from pants.base.specs import DirGlobSpec, RawSpecs
from pants.engine.addresses import Addresses
from pants.engine.fs import DigestSubset, PathGlobs
from pants.engine.internals.graph import hydrate_sources, resolve_targets
from pants.engine.internals.specs_rules import resolve_addresses_from_raw_specs
from pants.engine.intrinsics import digest_subset_to_digest, get_digest_contents
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    FieldSet,
    HydrateSourcesRequest,
    InferDependenciesRequest,
    InferredDependencies,
)
from pants.engine.unions import UnionRule
from pants_backend_cargo.target_types import (
    CargoPackageNameField,
    CargoPackageSourcesField,
    _CargoPackageMarker,
)
from pants_backend_cargo.util_rules.cargo import manifest_path
from pants_backend_cargo.util_rules.workspace import CargoPackageMapping


@dataclass(frozen=True)
class CargoDependenciesInferenceFieldSet(FieldSet):
    required_fields = (CargoPackageNameField, _CargoPackageMarker, CargoPackageSourcesField)

    sources: CargoPackageSourcesField


class InferCargoDependencies(InferDependenciesRequest):
    infer_from = CargoDependenciesInferenceFieldSet


_DEPENDENCY_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def _dependency_tables(manifest: dict) -> list[dict]:
    platforms = manifest.get("target", {}).values()
    return [scope.get(name, {}) for scope in (manifest, *platforms) for name in _DEPENDENCY_TABLES]


@rule(desc="Infer a cargo package's dependencies")
async def infer_cargo_dependencies(
    request: InferCargoDependencies, mapping: CargoPackageMapping
) -> InferredDependencies:
    address = request.field_set.address
    hydrated = await hydrate_sources(HydrateSourcesRequest(request.field_set.sources), **implicitly())
    contents = await get_digest_contents(
        await digest_subset_to_digest(
            DigestSubset(hydrated.snapshot.digest, PathGlobs([manifest_path(address.spec_path)]))
        )
    )

    dependencies = []
    workspace = mapping.workspace_for(address)
    if workspace is not None:
        dependencies.append(workspace)

    directories = sorted(
        {
            os.path.normpath(os.path.join(address.spec_path, dependency["path"]))
            for file_content in contents
            for table in _dependency_tables(tomllib.loads(file_content.content.decode()))
            for dependency in table.values()
            if isinstance(dependency, dict) and "path" in dependency
        }
    )
    address_sets = await concurrently(
        resolve_addresses_from_raw_specs(
            RawSpecs(
                dir_globs=(DirGlobSpec(directory),),
                description_of_origin=f"the `path` dependency `{directory}` of {address}",
            )
        )
        for directory in directories
    )
    candidates = await concurrently(
        resolve_targets(**implicitly({addresses: Addresses})) for addresses in address_sets
    )
    dependencies.extend(
        t.address for targets in candidates for t in targets if t.has_field(_CargoPackageMarker)
    )

    return InferredDependencies(sorted(d for d in dependencies if d != address))


def rules():
    return [*collect_rules(), UnionRule(InferDependenciesRequest, InferCargoDependencies)]
