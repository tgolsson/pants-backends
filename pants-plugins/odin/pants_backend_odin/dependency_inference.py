from __future__ import annotations

from pants.engine.rules import collect_rules, rule
from pants.engine.target import AllTargets, InferDependenciesRequest, InferredDependencies
from pants.engine.unions import UnionRule
from pants_backend_odin.target_types import InferOdinPackageDependenciesRequest, OdinSourceField


@rule
async def infer_odin_package_dependencies(
    request: InferOdinPackageDependenciesRequest, all_targets: AllTargets
) -> InferredDependencies:
    """Infer that odin_package targets depend on all odin_source targets in the same directory."""
    odin_package_address = request.field_set.address
    odin_package_spec_path = odin_package_address.spec_path

    # Find all odin_source targets in the same directory
    inferred_deps = []
    for target in all_targets:
        if (
            target.has_field(OdinSourceField)
            and target.address.spec_path == odin_package_spec_path
            and target.address != odin_package_address
        ):
            inferred_deps.append(target.address)

    return InferredDependencies(inferred_deps)


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferOdinPackageDependenciesRequest),
    ]
