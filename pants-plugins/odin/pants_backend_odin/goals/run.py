from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, OutputPathField
from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior, RunRequest
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants_backend_odin.goals.package import OdinPackageFieldSet
from pants_backend_odin.target_types import OdinDefinesField, OdinDependenciesField


@dataclass(frozen=True)
class OdinBinaryRunFieldSet(RunFieldSet):
    required_fields = (OdinDependenciesField, OutputPathField)
    run_in_sandbox_behavior = RunInSandboxBehavior.RUN_REQUEST_HERMETIC

    dependencies: OdinDependenciesField
    defines: OdinDefinesField
    output_path: OutputPathField


@rule
async def run_odin_binary(request: OdinBinaryRunFieldSet) -> RunRequest:
    """Run an Odin binary by first building it, then executing it."""

    # Wrap the target for package building
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.address, description_of_origin="run odin binary"),
    )

    # Create a package field set from the target to reuse existing build logic
    package_field_set = OdinPackageFieldSet.create(wrapped_target.target)

    # Build the binary using existing package infrastructure
    built_package = await Get(BuiltPackage, OdinPackageFieldSet, package_field_set)

    # The built package should contain exactly one artifact (the binary)
    if len(built_package.artifacts) != 1:
        raise ValueError(f"Expected exactly one artifact, got {len(built_package.artifacts)}")

    binary_path = built_package.artifacts[0].relpath
    return RunRequest(
        digest=built_package.digest,
        args=(f"{{chroot}}/{binary_path}",),
    )


def rules():
    return [
        *collect_rules(),
        *OdinBinaryRunFieldSet.rules(),
        UnionRule(RunFieldSet, OdinBinaryRunFieldSet),
    ]
