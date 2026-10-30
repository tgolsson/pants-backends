"""How a backend builds another backend's targets for the platform it is building for."""

from __future__ import annotations

import logging
from abc import ABCMeta
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact
from pants.engine.addresses import Address
from pants.engine.environment import EnvironmentName
from pants.engine.fs import (
    EMPTY_DIGEST,
    AddPrefix,
    Digest,
    DigestSubset,
    GlobMatchErrorBehavior,
    MergeDigests,
    PathGlobs,
)
from pants.engine.internals.graph import find_valid_field_sets
from pants.engine.intrinsics import add_prefix, digest_subset_to_digest, merge_digests
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import FieldSet, FieldSetsPerTargetRequest, Target
from pants.engine.unions import union
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.util_rules.platforms import (
    KnownPlatforms,
    SelectPlatformsRequest,
    TargetPlatform,
    select_platforms,
)

logger = logging.getLogger(__name__)


@union(in_scope_types=[EnvironmentName, TargetPlatform])
@dataclass(frozen=True)
class PlatformBuildFieldSet(FieldSet, metaclass=ABCMeta):
    """A member's rule takes the field set and a `TargetPlatform` and returns a `PlatformBuild`
    holding its files at the paths `pants package` writes them to for the host."""

    platforms: PlatformsField


@dataclass(frozen=True)
class PlatformBuild:
    digest: Digest
    files: tuple[str, ...]


def platform_build_files(build: PlatformBuild, description_of_origin: str) -> DigestSubset:
    """The build's `files` alone; a file it does not hold is an error naming its origin."""
    return DigestSubset(
        build.digest,
        PathGlobs(
            build.files,
            glob_match_error_behavior=GlobMatchErrorBehavior.error,
            description_of_origin=description_of_origin,
        ),
    )


@rule(polymorphic=True)
async def build_for_platform(
    field_set: PlatformBuildFieldSet, platform: TargetPlatform, environment_name: EnvironmentName
) -> PlatformBuild:
    raise NotImplementedError()


@dataclass(frozen=True)
class PlatformDependenciesRequest:
    dependent: Address
    targets: tuple[Target, ...]
    """Targets that are not `PlatformBuildFieldSet` members are ignored."""
    platform: TargetPlatform


@dataclass(frozen=True)
class PlatformDependencies:
    builds: FrozenDict[Address, PlatformBuild]


@rule(desc="Build dependencies for a platform", level=LogLevel.DEBUG)
async def build_platform_dependencies(
    request: PlatformDependenciesRequest, known: KnownPlatforms
) -> PlatformDependencies:
    per_target = await find_valid_field_sets(
        FieldSetsPerTargetRequest(PlatformBuildFieldSet, request.targets), **implicitly()
    )
    members = []
    for tgt, field_sets in zip(request.targets, per_target.collection):
        if len(field_sets) > 1:
            kinds = sorted(type(fs).__name__ for fs in field_sets)
            raise ValueError(f"{tgt.address} matches several platform build field sets: {kinds}")
        if not field_sets:
            continue
        listed = known.listed(field_sets[0].platforms)
        if request.platform not in listed:
            raise ValueError(
                f"{request.dependent} is being built for {request.platform.name}, but its "
                f"dependency {tgt.address} lists only {[p.name for p in listed]} in `platforms`."
            )
        members.append(field_sets[0])
    builds = await concurrently(
        build_for_platform(**implicitly({fs: PlatformBuildFieldSet, request.platform: TargetPlatform}))
        for fs in members
    )
    return PlatformDependencies(FrozenDict({fs.address: b for fs, b in zip(members, builds)}))


@dataclass(frozen=True)
class PackageForPlatformsRequest:
    field_set: PlatformBuildFieldSet


@rule(desc="Package for each platform", level=LogLevel.DEBUG)
async def package_for_platforms(request: PackageForPlatformsRequest, known: KnownPlatforms) -> BuiltPackage:
    """The host build at its own paths, every other selected platform under `<platform>/`."""
    field_set = request.field_set
    selected = await select_platforms(SelectPlatformsRequest(field_set.platforms), **implicitly())
    if not selected.platforms:
        logger.info(
            f"{field_set.address}: none of its `platforms` is the host ({known.host.name}) or "
            "enabled, so nothing is packaged."
        )
        return BuiltPackage(EMPTY_DIGEST, ())
    builds = await concurrently(
        build_for_platform(**implicitly({field_set: PlatformBuildFieldSet, p: TargetPlatform}))
        for p in selected.platforms
    )
    files = await concurrently(
        digest_subset_to_digest(platform_build_files(b, f"the build of {field_set.address} for {p.name}"))
        for p, b in zip(selected.platforms, builds)
    )
    digests, relpaths = [], []
    for platform, build, digest in zip(selected.platforms, builds, files):
        if platform == known.host:
            digests.append(digest)
            relpaths.extend(build.files)
        else:
            digests.append(await add_prefix(AddPrefix(digest, platform.name)))
            relpaths.extend(f"{platform.name}/{f}" for f in build.files)
    return BuiltPackage(
        await merge_digests(MergeDigests(digests)),
        tuple(BuiltPackageArtifact(relpath=r) for r in relpaths),
    )


def rules():
    return collect_rules()
