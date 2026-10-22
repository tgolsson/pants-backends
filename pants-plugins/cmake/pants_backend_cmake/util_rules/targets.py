"""From a `cmake_target`/`cmake_test` address to its configured project and discovered CMake
target, and the inferred dependency on the project (so `--changed-dependents` follows it)."""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address, AddressInput
from pants.engine.internals.build_files import maybe_resolve_address, resolve_address
from pants.engine.internals.graph import resolve_target
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    FieldSet,
    InferDependenciesRequest,
    InferredDependencies,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule

from pants_backend_cmake.ninja.graph import CMakeTargetInfo
from pants_backend_cmake.target_types import (
    CMakeProjectSourcesField,
    CMakeTargetNameField,
    CMakeTargetProjectField,
)
from pants_backend_cmake.util_rules.configure import (
    ConfiguredProject,
    ConfigureRequest,
    configure_project,
)


async def parse_address(value: str, relative_to: str, description_of_origin: str) -> Address:
    """One address string of a BUILD field, resolved as Pants does (relative to the BUILD
    file's directory)."""
    address_input = AddressInput.parse(
        value, relative_to=relative_to, description_of_origin=description_of_origin
    )
    maybe = await maybe_resolve_address(address_input)
    return await resolve_address(maybe)


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
    return InferredDependencies([address])


@dataclass(frozen=True)
class CMakeTargetRequest:
    address: Address


@dataclass(frozen=True)
class ResolvedCMakeTarget:
    project: ConfiguredProject
    name: str
    info: CMakeTargetInfo


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
    configured = await configure_project(ConfigureRequest(project_address), **implicitly())
    name = target[CMakeTargetNameField].value or request.address.target_name
    info = configured.targets.get(name)
    if info is None or not info.addressable:
        addressable = sorted(t.name for t in configured.targets.values() if t.addressable)
        raise ValueError(
            f"{request.address}: CMake target `{name}` is not a library or executable of "
            f"{project_address}. Addressable targets: {addressable}"
        )
    return ResolvedCMakeTarget(configured, name, info)


def rules():
    return [*collect_rules(), UnionRule(InferDependenciesRequest, InferCMakeProjectDependency)]
