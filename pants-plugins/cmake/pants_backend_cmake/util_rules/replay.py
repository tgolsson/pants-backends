"""Replay the Ninja edges a CMake target needs as individual cached processes.

One rule per edge: `edge_inputs` awaits the producers of the edge's declared inputs (other
edges, or whole dependency targets at a boundary) and collects exactly what the edge reads: the
workspace sources it declares, the headers its scan found, the files of `dependencies`, the
empty build directory, and its producers' outputs. `build_edge` runs the edge's command on
them; a linter can run its own command in the same sandbox. The engine schedules the
dataflow and memoizes each edge once per run, so two targets that share a compile edge (a
static and a shared library over the same object files) share one process.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.fs import (
    AddPrefix,
    CreateDigest,
    Digest,
    DigestSubset,
    FileContent,
    MergeDigests,
    PathGlobs,
)
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_subset_to_digest,
    merge_digests,
)
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_cmake.ninja.graph import UnsupportedGraphError, boundary_outputs, plan_target
from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER, rebase
from pants_backend_cmake.util_rules.configure import ConfiguredProject
from pants_backend_cmake.util_rules.scan import (
    ScanRequest,
    is_compile_edge,
    scan_translation_unit,
    workspace_path,
)


@dataclass(frozen=True)
class EdgeRequest:
    project: ConfiguredProject
    edge: NinjaEdge


@dataclass(frozen=True)
class EdgeOutput:
    digest: Digest
    """The edge's outputs, relative to the build root."""


@dataclass(frozen=True)
class BuildTargetRequest:
    project: ConfiguredProject
    name: str


@dataclass(frozen=True)
class BuiltTarget:
    name: str
    kind: str
    outputs: tuple[str, ...]
    """Final outputs, relative to the build root (under the project's build dir)."""
    digest: Digest
    """Exactly the final outputs."""
    runtime: Digest
    """The final outputs plus those of every target this one links against, transitively:
    what an executable needs next to it to run."""


def _resolve_inputs(
    project: ConfiguredProject, edge: NinjaEdge, boundary: dict[str, str]
) -> tuple[list[NinjaEdge], list[str], list[str]]:
    """The producer edges, dependency targets and workspace sources behind an edge's declared
    inputs, looking through phony edges."""
    producers = project.graph.producers
    edges: dict[NinjaEdge, None] = {}
    targets: dict[str, None] = {}
    sources: dict[str, None] = {}
    seen: set[str] = set()
    stack = list(edge.dependencies)
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        if path in boundary:
            targets[boundary[path]] = None
            continue
        producer = producers.get(path)
        if producer is None:
            workspace = workspace_path(path)
            if workspace is not None:
                sources[workspace] = None
        elif producer.is_phony:
            stack.extend(producer.dependencies)
        else:
            edges[producer] = None
    return list(edges), list(targets), list(sources)


@dataclass(frozen=True)
class EdgeInputs:
    digest: Digest
    """Everything the edge's command reads, relative to the build root."""


@rule(desc="Collect Ninja edge inputs", level=LogLevel.DEBUG)
async def edge_inputs(request: EdgeRequest) -> EdgeInputs:
    project, edge = request.project, request.edge
    boundary = boundary_outputs(project.targets, edge.target or "")
    producer_edges, dependency_targets, sources = _resolve_inputs(project, edge, boundary)
    produced, dependencies, scan = await concurrently(
        concurrently(build_edge(EdgeRequest(project, e), **implicitly()) for e in producer_edges),
        concurrently(
            build_target(BuildTargetRequest(project, t), **implicitly()) for t in dependency_targets
        ),
        scan_translation_unit(ScanRequest(project, edge), **implicitly())
        if is_compile_edge(edge)
        else _nothing(),
    )

    workspace = set(sources)
    if scan is not None:
        workspace.update(scan.headers)
    source_set = set(project.source_paths)
    extra_set = set(project.extra_paths)
    wanted_sources = sorted(p for p in workspace if p in source_set)
    wanted_extra = sorted(p for p in workspace if p in extra_set)

    digests: list[Digest] = [project.build_dir_digest, project.generated]
    digests.extend(p.digest for p in produced)
    digests.extend(d.digest for d in dependencies)
    if wanted_sources:
        digests.append(
            await digest_subset_to_digest(DigestSubset(project.sources, PathGlobs(wanted_sources)))
        )
    if wanted_extra:
        digests.append(await digest_subset_to_digest(DigestSubset(project.extra, PathGlobs(wanted_extra))))

    rspfile = project.graph.lookup(edge, "rspfile", shell=False)
    if rspfile:
        content = rebase(project.graph.lookup(edge, "rspfile_content"), project.relative_root)
        digests.append(
            await create_digest(
                CreateDigest([FileContent(f"{project.build_dir}/{rspfile}", content.encode())])
            )
        )
    return EdgeInputs(await merge_digests(MergeDigests(digests)))


@rule(desc="Build Ninja edge", level=LogLevel.DEBUG)
async def build_edge(request: EdgeRequest, bash: BashBinary) -> EdgeOutput:
    project, edge = request.project, request.edge
    graph = project.graph
    build_dir = project.build_dir
    relative_root = project.relative_root

    for output in edge.all_outputs:
        if output.startswith(PLACEHOLDER) or output.startswith(".."):
            raise UnsupportedGraphError(
                f"{project.address}: edge for {edge.outputs} writes `{output}` outside the build "
                "directory, which the replay cannot capture"
            )

    inputs = await edge_inputs(request)
    command = rebase(graph.command(edge), relative_root)

    output_dirs = sorted({os.path.dirname(o) for o in edge.all_outputs if os.path.dirname(o)})
    script = command
    if output_dirs:
        script = f"mkdir -p {' '.join(shlex.quote(d) for d in output_dirs)} && {command}"

    outputs = list(edge.all_outputs)
    if project.coverage and is_compile_edge(edge):
        outputs += [f"{o[:-2]}.gcno" for o in edge.outputs if o.endswith(".o")]

    env = dict(project.toolchain.env)
    immutable_input_digests = {}
    if "cmake" in shlex.split(command):
        env["PATH"] = f"{{chroot}}/{project.toolchain.cmake_bin_dir}{os.pathsep}{env['PATH']}"
        immutable_input_digests = dict(project.toolchain.immutable_input_digests)

    description = rebase(graph.lookup(edge, "description"), relative_root) or f"Build {edge.outputs[0]}"
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(bash.path, "-c", script),
                description=f"{description} ({project.address})",
                input_digest=inputs.digest,
                immutable_input_digests=immutable_input_digests,
                working_directory=build_dir,
                env=env,
                output_files=tuple(outputs),
                level=LogLevel.DEBUG,
            )
        )
    )
    return EdgeOutput(await add_prefix(AddPrefix(result.output_digest, build_dir)))


async def _nothing() -> None:
    return None


@rule(desc="Build CMake target")
async def build_target(request: BuildTargetRequest) -> BuiltTarget:
    project = request.project
    if request.name not in project.targets:
        raise ValueError(f"{project.address}: no CMake target `{request.name}`")
    # The plan validates the target (outputs, generated inputs) and names its dependencies.
    plan = plan_target(project.graph, request.name, project.targets)
    producers = project.graph.producers
    final_edges = {producers[o] for o in plan.outputs}
    built, dependencies = await concurrently(
        concurrently(build_edge(EdgeRequest(project, edge), **implicitly()) for edge in final_edges),
        concurrently(
            build_target(BuildTargetRequest(project, dep), **implicitly()) for dep in plan.dependencies
        ),
    )
    outputs = tuple(f"{project.build_dir}/{o}" for o in plan.outputs)
    merged = await merge_digests(MergeDigests([b.digest for b in built]))
    digest = await digest_subset_to_digest(DigestSubset(merged, PathGlobs(outputs)))
    runtime = await merge_digests(MergeDigests([digest, *(d.runtime for d in dependencies)]))
    return BuiltTarget(
        name=request.name,
        kind=project.targets[request.name].kind,
        outputs=outputs,
        digest=digest,
        runtime=runtime,
    )


def rules():
    return collect_rules()
