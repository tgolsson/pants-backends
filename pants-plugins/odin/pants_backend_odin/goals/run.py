from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import OutputPathField
from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior, RunRequest
from pants.engine.internals.graph import resolve_target
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants_backend_odin.goals.package import OdinPackageFieldSet, package_odin_application
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
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="run odin binary"), **implicitly()
    )

    # Create a package field set from the target to reuse existing build logic
    package_field_set = OdinPackageFieldSet.create(wrapped_target.target)

    # Build the binary using existing package infrastructure
    built_package = await package_odin_application(package_field_set)

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
