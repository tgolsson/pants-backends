"""Per-translation-unit header scan: which workspace headers a compile reads.

The compile's own compiler and flags run with `-M -MG` on its source, against the project's
header-like files and the files of its dependencies. The result is the exact input set for the
compile process, so an edit to a header rebuilds only the TUs that include it. Editing a
source rescans only that TU; editing a header rescans every TU (the header set is the scan's
input), which is cheap next to compiling. A scan that fails, or that names an include the
project does not provide, is an error: a silent fallback would hide a missing input until the
compile fails with a confusing message.

Two callers: the replay scans a Ninja compile edge with the edge's command; the test build
scans a test translation unit with the command it assembled, against the project's headers
plus the test's own files.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.engine.fs import Digest, DigestSubset, MergeDigests, PathGlobs
from pants.engine.intrinsics import digest_subset_to_digest, execute_process, merge_digests
from pants.engine.process import Process
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER, rebase
from pants_backend_cmake.util_rules.configure import ConfiguredProject

HEADER_SUFFIXES = (".h", ".hpp", ".hh", ".hxx", ".inc", ".inl", ".ipp", ".tcc", ".ixx", ".def")

# CMake's compile rules start with `${LAUNCHER}${CODE_CHECK}` (ccache, clang-tidy, ...); the
# scan runs the bare compiler.
SCAN_OVERRIDES = {"LAUNCHER": "", "CODE_CHECK": ""}


class ScanError(Exception):
    pass


def is_compile_edge(edge: NinjaEdge) -> bool:
    """CMake names compile rules `<LANG>_COMPILER__<target>...`. A depfile is not the test:
    since CMake 3.27 link edges have one too."""
    return "_COMPILER__" in edge.rule


def is_header_like(path: str) -> bool:
    base = os.path.basename(path)
    return "." not in base or base.endswith(HEADER_SUFFIXES)


def workspace_path(ninja_path: str) -> str | None:
    """A placeholder-rooted path as a build-root-relative path, else None."""
    prefix = PLACEHOLDER + "/"
    return ninja_path[len(prefix) :] if ninja_path.startswith(prefix) else None


def scan_argv(tokens: list[str]) -> list[str]:
    """A compile command with its output and depfile options replaced by `-M -MG`."""
    argv: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in ("-o", "-MT", "-MF", "-MQ"):
            skip = True
            continue
        if token in ("-c", "-MD", "-MMD"):
            continue
        argv.append(token)
    argv += ["-M", "-MG"]
    return argv


def depfile_entries(text: str, build_dir: str) -> list[tuple[str, str]]:
    """`(as written, build-root-relative)` for every dependency in Make-style output, which the
    compiler writes relative to its working directory; absolute paths dropped. A header `-MG`
    could not find is listed as spelled in the `#include`."""
    body = text.replace("\\\r\n", " ").replace("\\\n", " ")
    _, _, rest = body.partition(":")
    entries = []
    for token in rest.split():
        if os.path.isabs(token):
            continue
        entries.append((token, os.path.normpath(os.path.join(build_dir, token))))
    return entries


def parse_depfile(text: str, build_dir: str) -> list[str]:
    """Build-root-relative paths from Make-style dependency output; absolute paths dropped."""
    return [path for _, path in depfile_entries(text, build_dir)]


@dataclass(frozen=True)
class HeaderSetRequest:
    project: ConfiguredProject


@dataclass(frozen=True)
class HeaderSet:
    digest: Digest
    paths: tuple[str, ...]


@rule(level=LogLevel.DEBUG)
async def project_header_set(request: HeaderSetRequest) -> HeaderSet:
    project = request.project
    headers = tuple(p for p in project.source_paths if is_header_like(p))
    header_digest = await digest_subset_to_digest(DigestSubset(project.sources, PathGlobs(headers)))
    digest = await merge_digests(
        MergeDigests([header_digest, project.extra, project.generated, project.build_dir_digest])
    )
    return HeaderSet(digest, headers + project.extra_paths + project.generated_paths)


async def _scan(
    project: ConfiguredProject,
    owner: str,
    argv: list[str],
    what: str,
    input_digest: Digest,
    known: set[str],
    sources: set[str],
) -> tuple[str, ...]:
    """Run the scan; the build-root-relative headers it found, minus `sources` (the TU's own
    inputs). Fails on a non-zero exit or an include outside `known`, naming the include as the
    source spells it."""
    result = await execute_process(
        Process(
            argv=argv,
            description=f"Scan {what} ({owner})",
            input_digest=input_digest,
            working_directory=project.build_dir,
            env=project.toolchain.env,
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        raise ScanError(
            f"{owner}: header scan of {what} failed (exit {result.exit_code}):\n"
            f"{result.stderr.decode(errors='replace')}"
        )
    entries = depfile_entries(result.stdout.decode(), project.build_dir)
    unknown = sorted({spelled for spelled, path in entries if path not in known})
    if unknown:
        raise ScanError(
            f"{owner}: {what} includes files that none of its inputs provide: {unknown}. "
            "Inputs are the project's `sources` and the files of its dependencies and, for a "
            "test, its own `sources` and the files of its dependencies. Add the files to the right "
            "target, or if the build generates them, check them in or generate them with a "
            "codegen dependency."
        )
    return tuple(sorted({path for _, path in entries} - sources))


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
    header_set = await project_header_set(HeaderSetRequest(project))
    sources = {p for p in map(workspace_path, edge.dependencies) if p}
    globs = PathGlobs(sorted(sources))
    # A generated translation unit lives in `extra`.
    source_digest, extra_digest = await concurrently(
        digest_subset_to_digest(DigestSubset(project.sources, globs)),
        digest_subset_to_digest(DigestSubset(project.extra, globs)),
    )
    input_digest = await merge_digests(MergeDigests([source_digest, extra_digest, header_set.digest]))
    command = rebase(project.graph.command(edge, overrides=SCAN_OVERRIDES), project.relative_root)
    known = set(project.source_paths) | set(project.extra_paths) | set(project.generated_paths)
    headers = await _scan(
        project,
        str(project.address),
        scan_argv(shlex.split(command)),
        edge.outputs[0],
        input_digest,
        known,
        sources,
    )
    return ScanResult(headers)


@dataclass(frozen=True)
class ScanTestUnitRequest:
    project: ConfiguredProject
    owner: str
    """The test address, for messages."""
    argv: tuple[str, ...]
    """The compile command, with every path relative to the build directory."""
    source: str
    """The translation unit, relative to the build root."""
    files: Digest
    """The test's own files: its sources and headers and the files of its dependencies."""
    file_paths: tuple[str, ...]


@rule(desc="Scan test translation unit", level=LogLevel.DEBUG)
async def scan_test_translation_unit(request: ScanTestUnitRequest) -> ScanResult:
    project = request.project
    header_set = await project_header_set(HeaderSetRequest(project))
    input_digest = await merge_digests(MergeDigests([request.files, header_set.digest]))
    known = (
        set(project.source_paths)
        | set(project.extra_paths)
        | set(project.generated_paths)
        | set(request.file_paths)
    )
    headers = await _scan(
        project,
        request.owner,
        scan_argv(list(request.argv)),
        request.source,
        input_digest,
        known,
        {request.source},
    )
    return ScanResult(headers)


def rules():
    return collect_rules()
