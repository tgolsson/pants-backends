"""What a cargo process gets in its sandbox: the cargo sources of the transitive closure, the
`files`/`resources` in it, and, when asked for, the `package` output of any packageable target
in it (a library another backend builds, for example), staged at its package path and, when its
backend builds for platforms, built for the one cargo builds for. The closure
stops at such a target: its own dependencies are its backend's inputs, not cargo's. A workspace
root brings its member packages, which do not depend on it in the graph. A workspace reached
otherwise brings only its other members' cargo sources: cargo loads every member."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pants.core.goals.package import (
    EnvironmentAwarePackageRequest,
    PackageFieldSet,
    environment_aware_package,
)
from pants.core.target_types import FileSourceField, ResourceSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.graph import find_valid_field_sets, transitive_targets
from pants.engine.intrinsics import digest_subset_to_digest, merge_digests
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    Dependencies,
    DepsTraversalBehavior,
    FieldSetsPerTargetRequest,
    ShouldTraverseDepsPredicate,
    SpecialCasedDependencies,
    Target,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionMembership
from pants.util.frozendict import FrozenDict
from pants_backend_cargo.target_types import (
    CargoArtifactKindField,
    CargoEnvField,
    CargoPackageSourcesField,
    CargoWorkspaceSourcesField,
)
from pants_backend_cargo.util_rules.cargo import is_reserved_env
from pants_backend_cargo.util_rules.platform import RustTargetRequest, platform_triple, rust_target
from pants_backend_cargo.util_rules.workspace import CargoPackageMapping

from pants_backend_platforms.util_rules.contract import (
    PlatformDependenciesRequest,
    build_platform_dependencies,
    platform_build_files,
)
from pants_backend_platforms.util_rules.platforms import TargetPlatform

_CARGO_FIELDS = (CargoPackageSourcesField, CargoWorkspaceSourcesField)


@dataclass(frozen=True)
class CargoSourcesRequest:
    addresses: frozenset[Address]
    include_packages: bool
    """Whether to build the packageable targets in the closure and stage their outputs: what
    compiles needs them, `cargo fmt` and `cargo update` do not."""
    platform: TargetPlatform
    dependent: Address
    """Named in the error for a packageable target whose `platforms` lacks `platform`."""
    include_cargo_sources: bool = True
    """Whether to include the closure's cargo sources: `run` needs only its files, packages and
    `env`."""


@dataclass(frozen=True)
class CargoSources:
    digest: Digest
    files: tuple[str, ...]
    env: FrozenDict[str, str]
    """The `env` of every cargo package in the closure, merged: a dependency's build script
    reads what its own package set (`MYLIB_DIR`), whichever package the cargo process is
    for."""


def parse_env(entries: tuple[str, ...] | None) -> FrozenDict[str, str]:
    """A target's `env` field (`KEY=VALUE` entries) as a dict."""
    env = {}
    for entry in entries or ():
        key, sep, value = entry.partition("=")
        if not sep or not key:
            raise ValueError(f"`env` entries are `KEY=VALUE`, not `{entry}`")
        env[key] = value
    return FrozenDict(env)


def merge_env(targets: Iterable[Target], triples: Iterable[str]) -> FrozenDict[str, str]:
    """`triples` are those the cargo process builds for."""
    env: dict[str, str] = {}
    for target in targets:
        if not target.has_field(CargoEnvField):
            continue
        for key, value in parse_env(target[CargoEnvField].value).items():
            if key in ("CC", "CXX", "AR"):
                raise ValueError(
                    f"{target.address} sets `{key}` in `env`, but cargo builds take their C "
                    "tools from the `cc_toolchain` of the platform they build for, so it would "
                    "be ignored"
                )
            if is_reserved_env(key, triples):
                raise ValueError(
                    f"{target.address} sets `{key}` in `env`, but the cargo backend sets "
                    f"`{key}` itself for every cargo process"
                )
            if env.get(key, value) != value:
                raise ValueError(
                    f"{target.address} sets `{key}` in `env` to `{value}`, but another package "
                    f"of the same build sets it to `{env[key]}`"
                )
            env[key] = value
    return FrozenDict(env)


def _is_cargo(tgt: Target) -> bool:
    return tgt.has_field(CargoArtifactKindField) or any(tgt.has_field(f) for f in _CARGO_FIELDS)


@dataclass(frozen=True)
class _StopAtPackages(ShouldTraverseDepsPredicate):
    """Traverses everything but the dependencies of a packageable non-cargo target."""

    package_field_sets: tuple[type[PackageFieldSet], ...]

    def __call__(
        self, target: Target, field: Dependencies | SpecialCasedDependencies
    ) -> DepsTraversalBehavior:
        if not isinstance(field, Dependencies) or (
            not _is_cargo(target) and any(fs.is_applicable(target) for fs in self.package_field_sets)
        ):
            return DepsTraversalBehavior.EXCLUDE
        return DepsTraversalBehavior.INCLUDE


def _members(mapping: CargoPackageMapping, addresses: Iterable[Address]) -> set[Address]:
    return {m.package.address for a in addresses for m in mapping.workspace_to_packages.get(a, ())}


@rule(desc="Collect the sources of a cargo build")
async def cargo_sources(
    request: CargoSourcesRequest,
    mapping: CargoPackageMapping,
    union_membership: UnionMembership,
    platform: Platform,
) -> CargoSources:
    predicate = _StopAtPackages(tuple(union_membership.get(PackageFieldSet)))
    roots = set(request.addresses) | _members(mapping, request.addresses)
    closure = await transitive_targets(
        TransitiveTargetsRequest(sorted(roots), should_traverse_deps_predicate=predicate),
        **implicitly(),
    )
    in_closure = {tgt.address for tgt in closure.closure}
    others = _members(mapping, in_closure) - in_closure
    loaded: Iterable[Target] = ()
    if request.include_cargo_sources and others:
        loaded = (
            await transitive_targets(
                TransitiveTargetsRequest(sorted(others), should_traverse_deps_predicate=predicate),
                **implicitly(),
            )
        ).closure

    source_fields = [tgt[field] for tgt in loaded for field in _CARGO_FIELDS if tgt.has_field(field)]
    packageable = []
    for tgt in closure.closure:
        cargo_field = next((f for f in _CARGO_FIELDS if tgt.has_field(f)), None)
        if cargo_field is not None:
            if request.include_cargo_sources:
                source_fields.append(tgt[cargo_field])
        elif tgt.has_field(FileSourceField):
            source_fields.append(tgt[FileSourceField])
        elif tgt.has_field(ResourceSourceField):
            source_fields.append(tgt[ResourceSourceField])
        elif tgt.has_field(CargoArtifactKindField):
            continue  # cargo's own binaries are built by this process, not staged into it
        elif request.include_packages:
            packageable.append(tgt)

    sources, platform_builds, package_field_sets, target = await concurrently(
        determine_source_files(SourceFilesRequest(source_fields)),
        build_platform_dependencies(
            PlatformDependenciesRequest(request.dependent, tuple(packageable), request.platform),
            **implicitly(),
        ),
        find_valid_field_sets(FieldSetsPerTargetRequest(PackageFieldSet, packageable), **implicitly()),
        rust_target(RustTargetRequest(request.platform), **implicitly()),
    )
    packages, built = await concurrently(
        concurrently(
            environment_aware_package(EnvironmentAwarePackageRequest(fs))
            for fs in package_field_sets.field_sets
            if fs.address not in platform_builds.builds
        ),
        concurrently(
            digest_subset_to_digest(
                platform_build_files(b, f"the build of {address} for {request.platform.name}")
            )
            for address, b in platform_builds.builds.items()
        ),
    )
    digest = await merge_digests(
        MergeDigests([sources.snapshot.digest, *(p.digest for p in packages), *built])
    )
    triples = (target.triple, platform_triple(platform))
    return CargoSources(digest, sources.files, merge_env(closure.closure, triples))


def rules():
    return collect_rules()
