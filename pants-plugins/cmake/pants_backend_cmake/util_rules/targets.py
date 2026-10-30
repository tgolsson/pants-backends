"""From a `cmake_target` address to its configured project and discovered CMake
target, and the inferred dependency on the project (so `--changed-dependents` follows it)."""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.internals.graph import (
    ResolveTargetGeneratorRequests,
    resolve_generator_target_requests,
    resolve_target,
)
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    FieldSet,
    InferDependenciesRequest,
    InferredDependencies,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule

from pants_backend_cc.util_rules.fields import parse_address
from pants_backend_cmake.ninja.graph import CMakeTargetInfo
from pants_backend_cmake.target_types import (
    CMakeBuildTypeField,
    CMakeCoverageArgsField,
    CMakeProjectSourcesField,
    CMakeTargetNameField,
    CMakeTargetProjectField,
)
from pants_backend_cmake.util_rules.configure import (
    ConfiguredProject,
    ConfigureRequest,
    configure_project,
    generated_by,
)
from pants_backend_platforms.util_rules.platforms import TargetPlatform


async def _project_address(field: CMakeTargetProjectField, address: Address) -> Address:
    return await parse_address(field.value, address.spec_path, f"the `{field.alias}` field of {address}")


@dataclass(frozen=True)
class CMakeProjectInferenceFieldSet(FieldSet):
    required_fields = (CMakeTargetProjectField,)

    project: CMakeTargetProjectField


class InferCMakeProjectDependency(InferDependenciesRequest):
    infer_from = CMakeProjectInferenceFieldSet


@rule(desc="Infer the cmake_project dependency of a CMake target")
async def infer_cmake_project_dependency(request: InferCMakeProjectDependency) -> InferredDependencies:
    address = await _project_address(request.field_set.project, request.field_set.address)
    # A generated handle reaches its project's inputs through `#_inputs`; an edge to its own
    # generator would cycle (the generator depends on everything it generates).
    if generated_by(request.field_set.address, address):
        return InferredDependencies([])
    return InferredDependencies([address])


@dataclass(frozen=True)
class CMakeTargetRequest:
    address: Address
    platform: TargetPlatform
    coverage: bool = False
    """The project's build for coverage consumers, when it has `coverage_cmake_args`."""


@dataclass(frozen=True)
class ResolvedCMakeTarget:
    project: ConfiguredProject
    name: str
    info: CMakeTargetInfo


async def _project_build_type(project_address: Address) -> str | None:
    """The `build_type` the project gives the targets it generates, for the parametrization
    `project_address` names (`:proj@build_type=Release`), or `None` when it names none of them.

    `build_type` moves from the project to what it generates, so the project target no longer has
    it; the generation templates do, before any configure runs.
    """
    resolved = await resolve_generator_target_requests(
        ResolveTargetGeneratorRequests(
            project_address.maybe_convert_to_target_generator(), "the `project` of a `cmake_target`"
        ),
        **implicitly(),
    )
    for generate in resolved.requests:
        if generate.template_address == project_address:
            return generate.template.get(CMakeBuildTypeField.alias)
    return None


@rule
async def resolve_cmake_target(request: CMakeTargetRequest) -> ResolvedCMakeTarget:
    wrapped = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="cmake target"), **implicitly()
    )
    target = wrapped.target
    project_address = await _project_address(target[CMakeTargetProjectField], request.address)
    project_wrapped = await resolve_target(
        WrappedTargetRequest(
            project_address, description_of_origin=f"the `project` field of {request.address}"
        ),
        **implicitly(),
    )
    if not project_wrapped.target.has_field(CMakeProjectSourcesField):
        raise ValueError(f"{request.address}: `project` must name a `cmake_project`, not {project_address}")
    # `get`: other backends' targets reach this rule without a `build_type` field.
    build_type = target.get(CMakeBuildTypeField).value or await _project_build_type(project_address)
    coverage = request.coverage and bool(project_wrapped.target[CMakeCoverageArgsField].value)
    configured = await configure_project(
        ConfigureRequest(project_address, request.platform, build_type, coverage), **implicitly()
    )
    name = target[CMakeTargetNameField].value or request.address.target_name
    info = configured.targets.get(name)
    if info is None or not info.addressable:
        addressable = sorted(t.name for t in configured.targets.values() if t.addressable)
        raise ValueError(
            f"{request.address}: CMake target `{name}` is not a library or executable of "
            f"{project_address} for {request.platform.name}. Addressable targets: {addressable}"
        )
    return ResolvedCMakeTarget(configured, name, info)


def rules():
    return [*collect_rules(), UnionRule(InferDependenciesRequest, InferCMakeProjectDependency)]
