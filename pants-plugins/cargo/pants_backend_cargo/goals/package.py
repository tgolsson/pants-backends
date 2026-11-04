"""`pants package` of a `cargo_binary`: `dist/<path>/<binary name>` for the host, and
`dist/<platform>/<path>/<binary name>` for each other platform built."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

from pants.core.environments.target_types import EnvironmentField
from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior
from pants.engine.fs import AddPrefix
from pants.engine.intrinsics import add_prefix
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import Target
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants_backend_cargo.internal.build import CargoBinaryRequest, build_cargo_binary
from pants_backend_cargo.subsystems import RustSubsystem
from pants_backend_cargo.target_types import (
    CargoArtifactKindField,
    CargoArtifactNameField,
    CargoEnvField,
)

from pants_backend_platforms.util_rules.contract import (
    PackageForPlatformsRequest,
    PlatformBuild,
    PlatformBuildFieldSet,
    package_for_platforms,
)
from pants_backend_platforms.util_rules.platforms import TargetPlatform


@dataclass(frozen=True)
class CargoBinaryFieldSet(PackageFieldSet, RunFieldSet, PlatformBuildFieldSet):
    required_fields = (CargoArtifactKindField, CargoArtifactNameField, OutputPathField)
    run_in_sandbox_behavior = RunInSandboxBehavior.RUN_REQUEST_HERMETIC

    kind: CargoArtifactKindField
    artifact_name: CargoArtifactNameField
    output_path: OutputPathField
    env: CargoEnvField
    environment: EnvironmentField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return tgt[CargoArtifactKindField].value != "bin"


@rule(desc="Build a cargo binary for a platform", level=LogLevel.DEBUG)
async def build_cargo_binary_for_platform(
    field_set: CargoBinaryFieldSet, platform: TargetPlatform, rust: RustSubsystem
) -> PlatformBuild:
    binary = await build_cargo_binary(
        CargoBinaryRequest(field_set.address, field_set.artifact_name.value, rust.release, platform),
        **implicitly(),
    )
    # `output_path` names the directory (unset, the default template's directory,
    # `path.to.target/`); the file keeps cargo's name for the binary.
    output_path = field_set.output_path
    path = PurePath(output_path.value_or_default(file_ending=None))
    directory = path.parent if output_path.value == output_path.default else path
    digest = await add_prefix(AddPrefix(binary.digest, str(directory)))
    return PlatformBuild(digest, (str(directory / binary.name),))


@rule(desc="Package a cargo binary", level=LogLevel.DEBUG)
async def package_cargo_binary(field_set: CargoBinaryFieldSet) -> BuiltPackage:
    return await package_for_platforms(PackageForPlatformsRequest(field_set), **implicitly())


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, CargoBinaryFieldSet),
        UnionRule(PlatformBuildFieldSet, CargoBinaryFieldSet),
    ]
