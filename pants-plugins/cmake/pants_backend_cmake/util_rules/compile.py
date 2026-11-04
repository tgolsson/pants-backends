"""A `cmake_project`'s compile edges as `pants_backend_cc` compile steps, each in the sandbox the
replay compiles it in."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_cc.lint.clang_tidy.target_types import ClangTidyPathsField
from pants_backend_cc.util_rules.compile import (
    CompileInputs,
    CompileInputsRequest,
    CompileStep,
    CompileSteps,
    CompileStepsFieldSet,
)
from pants_backend_cc.util_rules.fields import parse_address
from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.target_types import (
    CMakeBuildTypeField,
    CMakeProjectInputsDependenciesField,
    CMakeProjectInputsTarget,
    CMakeProjectTarget,
    CMakeTargetProjectField,
)
from pants_backend_cmake.util_rules.canonicalize import rebase
from pants_backend_cmake.util_rules.configure import (
    ConfiguredProject,
    ConfigureRequest,
    configure_project,
)
from pants_backend_cmake.util_rules.link import edge_language
from pants_backend_cmake.util_rules.probes import PROBE_PREFIX
from pants_backend_cmake.util_rules.replay import EdgeRequest, edge_inputs
from pants_backend_cmake.util_rules.scan import SCAN_OVERRIDES, is_compile_edge, workspace_path
from pants_backend_platforms.util_rules.platforms import TargetPlatform


@dataclass(frozen=True)
class CMakeCompileStepsFieldSet(CompileStepsFieldSet):
    """A project's compile steps, on its `#_inputs`: goals replace the project with the targets
    it generates."""

    required_fields = (CMakeProjectInputsDependenciesField, CMakeTargetProjectField)

    project: CMakeTargetProjectField
    build_type: CMakeBuildTypeField


@dataclass(frozen=True)
class CMakeCompileInputsRequest(CompileInputsRequest):
    project: ConfiguredProject
    edge: NinjaEdge


def compile_step(project: ConfiguredProject, edge: NinjaEdge) -> CompileStep | None:
    """The edge as a compile step when it compiles C, C++ or assembler for the project rather than
    for a consumer probe. Its `-c`, `-o` and depfile options stay; a launcher does not."""
    if not is_compile_edge(edge) or not edge.inputs or (edge.target or "").startswith(PROBE_PREFIX):
        return None
    language = edge_language(edge)
    if language is None:
        return None
    build_dir = project.build_dir
    source = workspace_path(edge.inputs[0]) or os.path.normpath(os.path.join(build_dir, edge.inputs[0]))
    command = project.graph.command(edge, overrides=SCAN_OVERRIDES)
    return CompileStep(
        owner=project.address,
        source=source,
        generated=source.startswith(f"{build_dir}/"),
        object=os.path.normpath(os.path.join(build_dir, edge.outputs[0])),
        language=language,
        argv=tuple(shlex.split(rebase(command, project.relative_root))),
        cwd=build_dir,
        env=project.toolchain.env,
        inputs=CMakeCompileInputsRequest(project, edge),
    )


@rule(desc="List CMake compile steps", level=LogLevel.DEBUG)
async def cmake_compile_steps(field_set: CMakeCompileStepsFieldSet, platform: TargetPlatform) -> CompileSteps:
    project_address = await parse_address(
        field_set.project.value, field_set.address.spec_path, f"the `project` field of {field_set.address}"
    )
    project = await configure_project(
        ConfigureRequest(project_address, platform, field_set.build_type.value), **implicitly()
    )
    steps = (compile_step(project, edge) for edge in project.graph.edges)
    return CompileSteps(step for step in steps if step is not None)


@rule(desc="Collect CMake compile inputs", level=LogLevel.DEBUG)
async def cmake_compile_inputs(request: CMakeCompileInputsRequest) -> CompileInputs:
    inputs = await edge_inputs(EdgeRequest(request.project, request.edge), **implicitly())
    return CompileInputs(inputs.digest)


def rules():
    return [
        *collect_rules(),
        UnionRule(CompileStepsFieldSet, CMakeCompileStepsFieldSet),
        UnionRule(CompileInputsRequest, CMakeCompileInputsRequest),
        # Inert unless `pants_backend_cc.lint.clang_tidy` is enabled.
        *(
            target.register_plugin_field(ClangTidyPathsField)
            for target in (CMakeProjectTarget, CMakeProjectInputsTarget)
        ),
    ]
