"""How consumers use a `cmake_target` library (`pants_backend_cc.util_rules.library`), read from
the consumer probes (`probes.py`): the compile and link edges CMake generated for a hidden
executable linking each library. So a consumer sees a library exactly as any CMake consumer
does: its PUBLIC and INTERFACE defines, include directories and options, the directory's global
flags for the language, and the full transitive link line; its private requirements do not
leak. Consumers compile and link in the project's build directory, where those flags and paths
apply unchanged.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.engine.fs import MergeDigests
from pants.engine.intrinsics import merge_digests
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.unions import UnionRule
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_cc.util_rules.compile import ASM, CXX, C
from pants_backend_cc.util_rules.library import (
    CcCompileInterface,
    CcConsumer,
    CcLibraryCwd,
    CcLibraryError,
    CcLibraryFieldSet,
    CcLinkInterface,
)
from pants_backend_cc.util_rules.scan import without_outputs
from pants_backend_cmake.ninja.graph import (
    CMakeTargetInfo,
    boundary_outputs,
    is_link_edge,
    target_edges,
)
from pants_backend_cmake.ninja.parser import NinjaEdge, NinjaGraph
from pants_backend_cmake.target_types import CMakeTargetNameField, CMakeTargetProjectField
from pants_backend_cmake.util_rules.canonicalize import rebase
from pants_backend_cmake.util_rules.probes import probe_name
from pants_backend_cmake.util_rules.replay import BuildTargetRequest, build_target
from pants_backend_cmake.util_rules.scan import (
    SCAN_OVERRIDES,
    HeaderSetRequest,
    is_compile_edge,
    project_header_set,
)
from pants_backend_cmake.util_rules.targets import (
    CMakeTargetRequest,
    ResolvedCMakeTarget,
    resolve_cmake_target,
)
from pants_backend_platforms.util_rules.platforms import TargetPlatform

_LIBRARY_KINDS = frozenset({"STATIC_LIBRARY", "SHARED_LIBRARY"})
_LINK_DEPFILE = "-Wl,--dependency-file="


def edge_language(edge: NinjaEdge) -> str | None:
    if edge.rule.startswith("C_"):
        return C
    if edge.rule.startswith("CXX_"):
        return CXX
    if edge.rule.startswith("ASM"):
        return ASM
    return None


def compile_flags(graph: NinjaGraph, edge: NinjaEdge, relative_root: str) -> tuple[str, ...]:
    """A compile edge's command without the compiler, the source, the output and the depfile
    options, a response file expanded in place. Paths relative to the build directory."""
    command = rebase(graph.command(edge, overrides=SCAN_OVERRIDES), relative_root)
    tokens = shlex.split(command)
    rspfile = graph.lookup(edge, "rspfile", shell=False)
    if rspfile:
        content = rebase(graph.lookup(edge, "rspfile_content"), relative_root)
        tokens = [
            expanded
            for token in tokens
            for expanded in (shlex.split(content) if token == f"@{rspfile}" else [token])
        ]
    inputs = {rebase(p, relative_root) for p in edge.inputs}
    return tuple(without_outputs(tokens[1:], drop=inputs))


def _probe_edges(
    graph: NinjaGraph, targets: FrozenDict[str, CMakeTargetInfo], library: str, lang: str
) -> tuple[NinjaEdge, NinjaEdge]:
    """The compile and link edges of the probe for `library` in `lang`."""
    name = probe_name(library, lang)
    if name not in targets:
        raise CcLibraryError(
            f"no consumer probe `{name}` for CMake target `{library}`: its project does not "
            "enable the language"
        )
    edges = target_edges(graph, name)
    compile_edges = [e for e in edges if is_compile_edge(e)]
    link_edges = [e for e in edges if is_link_edge(e)]
    if len(compile_edges) != 1 or len(link_edges) != 1:
        raise CcLibraryError(f"consumer probe `{name}` has an unexpected shape: {edges}")
    return compile_edges[0], link_edges[0]


def consumer_compile_flags(
    graph: NinjaGraph,
    targets: FrozenDict[str, CMakeTargetInfo],
    relative_root: str,
    library: str,
    lang: str,
) -> tuple[str, ...]:
    """What a consumer of `library` compiles a `lang` translation unit with."""
    compile_edge, _ = _probe_edges(graph, targets, library, lang)
    return compile_flags(graph, compile_edge, relative_root)


@dataclass(frozen=True)
class ConsumerLink:
    driver: str
    """The language whose compiler CMake links the consumer with."""
    flags: tuple[str, ...]
    libraries: tuple[str, ...]
    """The link line after the objects, relative to the build directory: the library's outputs,
    everything behind them, system libraries."""


def consumer_link(
    graph: NinjaGraph,
    targets: FrozenDict[str, CMakeTargetInfo],
    relative_root: str,
    library: str,
    lang: str,
) -> ConsumerLink:
    """What a consumer of `library` written in `lang` is linked with."""
    _, link_edge = _probe_edges(graph, targets, library, lang)
    driver = edge_language(link_edge)
    if driver is None:
        raise CcLibraryError(f"cannot tell the link language of `{link_edge.rule}`")

    def tokens(name: str) -> list[str]:
        return shlex.split(rebase(graph.lookup(link_edge, name), relative_root))

    # CMake >= 3.27 asks the linker for a dependency file of the probe's own link; not ours.
    flags = [t for t in tokens("FLAGS") + tokens("LINK_FLAGS") if not t.startswith(_LINK_DEPFILE)]
    return ConsumerLink(
        driver=driver,
        flags=tuple(flags),
        libraries=tuple(tokens("LINK_PATH") + tokens("LINK_LIBRARIES")),
    )


@dataclass(frozen=True)
class CMakeLibraryFieldSet(CcLibraryFieldSet):
    required_fields = (CMakeTargetProjectField, CMakeTargetNameField)


async def _library(field_set: CMakeLibraryFieldSet, platform: TargetPlatform) -> ResolvedCMakeTarget:
    resolved = await resolve_cmake_target(CMakeTargetRequest(field_set.address, platform), **implicitly())
    if resolved.info.kind not in _LIBRARY_KINDS:
        raise CcLibraryError(
            f"cannot link against CMake target `{resolved.name}` of {field_set.address} "
            f"({resolved.info.kind}); only static and shared libraries have consumers"
        )
    return resolved


@rule(desc="Read where consumers of a CMake library work", level=LogLevel.DEBUG)
async def cmake_library_cwd(
    field_set: CMakeLibraryFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcLibraryCwd:
    resolved = await _library(field_set, platform)
    return CcLibraryCwd(resolved.project.build_dir)


@rule(desc="Read the compile interface of a CMake library", level=LogLevel.DEBUG)
async def cmake_compile_interface(
    field_set: CMakeLibraryFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcCompileInterface:
    resolved = await _library(field_set, platform)
    project = resolved.project
    flags = consumer_compile_flags(
        project.graph, project.targets, project.relative_root, resolved.name, consumer.language
    )
    universe, files = await concurrently(
        project_header_set(HeaderSetRequest(project)),
        merge_digests(MergeDigests([project.sources, project.extra])),
    )
    return CcCompileInterface(
        flags=flags,
        headers=universe.digest,
        files=files,
        file_paths=universe.paths,
        generated=project.generated,
    )


@rule(desc="Read the link interface of a CMake library", level=LogLevel.DEBUG)
async def cmake_link_interface(
    field_set: CMakeLibraryFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcLinkInterface:
    resolved = await _library(field_set, platform)
    project = resolved.project
    graph, targets = project.graph, project.targets
    link = consumer_link(graph, targets, project.relative_root, resolved.name, consumer.language)
    # CMake's build-tree rpath; a consumer sets its own.
    args = tuple(t for t in link.libraries if not t.startswith("-Wl,-rpath,"))
    boundary = boundary_outputs(targets, "")
    names = dict.fromkeys(boundary[t] for t in args if t in boundary)
    built = await concurrently(
        build_target(BuildTargetRequest(project, name), **implicitly()) for name in names
    )
    libraries, runtime = await concurrently(
        merge_digests(MergeDigests(b.digest for b in built)),
        merge_digests(MergeDigests(b.runtime for b in built)),
    )
    shared = (t for t in args if t in boundary and targets[boundary[t]].kind == "SHARED_LIBRARY")
    return CcLinkInterface(
        driver=link.driver,
        flags=link.flags,
        args=args,
        libraries=libraries,
        runtime=runtime,
        runtime_dirs=tuple(dict.fromkeys(os.path.dirname(t) for t in shared)),
    )


def rules():
    return [*collect_rules(), UnionRule(CcLibraryFieldSet, CMakeLibraryFieldSet)]
