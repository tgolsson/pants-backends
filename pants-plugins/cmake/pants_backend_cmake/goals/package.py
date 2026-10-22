from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule

from pants_backend_cmake.target_types import CMakeTargetNameField, CMakeTargetProjectField
from pants_backend_cmake.util_rules.replay import BuildTargetRequest, build_target
from pants_backend_cmake.util_rules.targets import CMakeTargetRequest, resolve_cmake_target


@dataclass(frozen=True)
class CMakeTargetFieldSet(PackageFieldSet):
    required_fields = (CMakeTargetProjectField, CMakeTargetNameField)

    project: CMakeTargetProjectField
    cmake_name: CMakeTargetNameField


@rule(desc="Package CMake target")
async def package_cmake_target(field_set: CMakeTargetFieldSet) -> BuiltPackage:
    resolved = await resolve_cmake_target(CMakeTargetRequest(field_set.address), **implicitly())
    built = await build_target(BuildTargetRequest(resolved.project, resolved.name), **implicitly())
    return BuiltPackage(built.digest, tuple(BuiltPackageArtifact(relpath=output) for output in built.outputs))


def rules():
    return [*collect_rules(), UnionRule(PackageFieldSet, CMakeTargetFieldSet)]
