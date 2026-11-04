from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule

from pants_backend_cmake.target_types import CMakeTargetNameField, CMakeTargetProjectField
from pants_backend_cmake.util_rules.replay import BuildTargetRequest, build_target
from pants_backend_cmake.util_rules.targets import CMakeTargetRequest, resolve_cmake_target
from pants_backend_platforms.util_rules.contract import (
    PackageForPlatformsRequest,
    PlatformBuild,
    PlatformBuildFieldSet,
    package_for_platforms,
)
from pants_backend_platforms.util_rules.platforms import TargetPlatform


@dataclass(frozen=True)
class CMakeTargetFieldSet(PackageFieldSet, PlatformBuildFieldSet):
    required_fields = (CMakeTargetProjectField, CMakeTargetNameField)

    project: CMakeTargetProjectField
    cmake_name: CMakeTargetNameField


@rule(desc="Build CMake target for a platform")
async def build_cmake_target_for_platform(
    field_set: CMakeTargetFieldSet, platform: TargetPlatform
) -> PlatformBuild:
    resolved = await resolve_cmake_target(CMakeTargetRequest(field_set.address, platform), **implicitly())
    built = await build_target(BuildTargetRequest(resolved.project, resolved.name), **implicitly())
    return PlatformBuild(built.digest, built.outputs)


@rule(desc="Package CMake target")
async def package_cmake_target(field_set: CMakeTargetFieldSet) -> BuiltPackage:
    return await package_for_platforms(PackageForPlatformsRequest(field_set), **implicitly())


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, CMakeTargetFieldSet),
        UnionRule(PlatformBuildFieldSet, CMakeTargetFieldSet),
    ]
