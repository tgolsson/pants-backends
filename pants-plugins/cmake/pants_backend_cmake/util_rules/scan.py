"""The header scan (`pants_backend_cc.util_rules.scan`) of a configured project's compile edges."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from pants.engine.fs import DigestSubset, MergeDigests, PathGlobs
from pants.engine.intrinsics import digest_subset_to_digest, merge_digests
from pants.engine.rules import collect_rules, concurrently, rule
from pants.util.logging import LogLevel

from pants_backend_cc.util_rules.scan import HeaderUniverse, is_header_like, scan_headers
from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER, rebase
from pants_backend_cmake.util_rules.configure import ConfiguredProject

# CMake's compile rules start with `${LAUNCHER}${CODE_CHECK}` (ccache, clang-tidy, ...); the
# scan runs the bare compiler.
SCAN_OVERRIDES = {"LAUNCHER": "", "CODE_CHECK": ""}


def is_compile_edge(edge: NinjaEdge) -> bool:
    """CMake names compile rules `<LANG>_COMPILER__<target>...`. A depfile is not the test:
    since CMake 3.27 link edges have one too."""
    return "_COMPILER__" in edge.rule


def workspace_path(ninja_path: str) -> str | None:
    """A placeholder-rooted path as a build-root-relative path, else None."""
    prefix = PLACEHOLDER + "/"
    return ninja_path[len(prefix) :] if ninja_path.startswith(prefix) else None


@dataclass(frozen=True)
class HeaderSetRequest:
    project: ConfiguredProject


@rule(level=LogLevel.DEBUG)
async def project_header_set(request: HeaderSetRequest) -> HeaderUniverse:
    project = request.project
    headers = tuple(p for p in project.source_paths if is_header_like(p))
    header_digest = await digest_subset_to_digest(DigestSubset(project.sources, PathGlobs(headers)))
    digest = await merge_digests(
        MergeDigests([header_digest, project.extra, project.generated, project.build_dir_digest])
    )
    return HeaderUniverse(
        digest,
        project.source_paths + project.extra_paths + project.generated_paths,
        project.build_dir,
        project.toolchain.env,
    )


@dataclass(frozen=True)
class ScanRequest:
    project: ConfiguredProject
    edge: NinjaEdge


@dataclass(frozen=True)
class ScanResult:
    headers: tuple[str, ...]


@rule(desc="Scan translation unit", level=LogLevel.DEBUG)
async def scan_translation_unit(request: ScanRequest) -> ScanResult:
    project, edge = request.project, request.edge
    universe = await project_header_set(HeaderSetRequest(project))
    sources = {p for p in map(workspace_path, edge.dependencies) if p}
    globs = PathGlobs(sorted(sources))
    # A generated translation unit lives in `extra`.
    source_digest, extra_digest = await concurrently(
        digest_subset_to_digest(DigestSubset(project.sources, globs)),
        digest_subset_to_digest(DigestSubset(project.extra, globs)),
    )
    command = rebase(project.graph.command(edge, overrides=SCAN_OVERRIDES), project.relative_root)
    headers = await scan_headers(
        universe,
        str(project.address),
        shlex.split(command),
        edge.outputs[0],
        (source_digest, extra_digest),
        (),
        sources,
    )
    return ScanResult(headers)


def rules():
    return collect_rules()
