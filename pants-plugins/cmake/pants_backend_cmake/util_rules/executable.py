"""Compile and link a `cmake_test`: its translation units against the `cmake_target` libraries
in its `dependencies`, in those libraries' build directory. CMake builds the libraries; Pants
builds the test.

What a translation unit is compiled with, and what the executable is linked with, comes from
the consumer probes (`probes.py`): the compile and link edges CMake generated for a hidden
executable linking each library. So a test sees a library exactly as any consumer does: its
PUBLIC and INTERFACE defines, include directories and options, the directory's global flags
for the language, and the full transitive link line. Private requirements of the library do
not leak. With several libraries, the first declared one's probe gives the base flags and the
others add their own usage requirements; the link lines are concatenated in declared order.
The test adds `include_dirs`, then `-I<root>` for each source root owning one of its files or
its dependencies' files (so a vendored framework under its own root needs no `include_dirs`),
`defines`, `compiler_args` and `linker_args`. The link uses the C++ driver when the test has
C++ translation units or CMake links a C consumer of one of the libraries with it.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.backend.cc.target_types import CCSourceField
from pants.core.target_types import FileSourceField, ResourceSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import (
    AddPrefix,
    CreateDigest,
    Digest,
    DigestSubset,
    Directory,
    MergeDigests,
    PathGlobs,
)
from pants.engine.internals.graph import resolve_target, resolve_targets
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_subset_to_digest,
    merge_digests,
)
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import DependenciesRequest, SourcesField, Target, WrappedTargetRequest
from pants.source.source_root import SourceRootsRequest, get_optional_source_roots
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_cmake.ninja.graph import (
    CMakeTargetInfo,
    boundary_outputs,
    is_link_edge,
    target_edges,
)
from pants_backend_cmake.ninja.parser import NinjaEdge, NinjaGraph
from pants_backend_cmake.target_types import (
    CMakeTargetProjectField,
    CMakeTestCompilerArgsField,
    CMakeTestDefinesField,
    CMakeTestDependenciesField,
    CMakeTestIncludeDirsField,
    CMakeTestLinkerArgsField,
    CMakeTestSourcesField,
    is_translation_unit,
)
from pants_backend_cmake.util_rules.canonicalize import rebase
from pants_backend_cmake.util_rules.configure import ConfiguredProject, overlap_error
from pants_backend_cmake.util_rules.probes import probe_name
from pants_backend_cmake.util_rules.replay import BuildTargetRequest, build_target
from pants_backend_cmake.util_rules.scan import (
    SCAN_OVERRIDES,
    ScanTestUnitRequest,
    is_compile_edge,
    scan_test_translation_unit,
)
from pants_backend_cmake.util_rules.targets import (
    CMakeTargetRequest,
    parse_address,
    resolve_cmake_target,
)
from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.util_rules.platforms import KnownPlatforms

C = "c"
CXX = "cxx"
_CXX_SUFFIXES = (".cc", ".cpp", ".cxx", ".c++")
_SHARED_SUFFIXES = (".so", ".dylib")
_LIBRARY_KINDS = frozenset({"STATIC_LIBRARY", "SHARED_LIBRARY"})
_LINK_DEPFILE = "-Wl,--dependency-file="
OUTPUT_DIR = "__pants_tests"


class TestBuildError(Exception):
    pass


def language(path: str) -> str:
    return CXX if path.endswith(_CXX_SUFFIXES) else C


def edge_language(edge: NinjaEdge) -> str | None:
    if edge.rule.startswith("C_"):
        return C
    if edge.rule.startswith("CXX_"):
        return CXX
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
    flags: list[str] = []
    skip = False
    for token in tokens[1:]:
        if skip:
            skip = False
            continue
        if token in ("-o", "-MT", "-MF", "-MQ"):
            skip = True
            continue
        if token in ("-c", "-MD", "-MMD") or token in inputs:
            continue
        flags.append(token)
    return tuple(flags)


def _probe_edges(
    graph: NinjaGraph, targets: FrozenDict[str, CMakeTargetInfo], library: str, lang: str
) -> tuple[NinjaEdge, NinjaEdge]:
    """The compile and link edges of the probe for `library` in `lang`."""
    name = probe_name(library, lang)
    if name not in targets:
        raise TestBuildError(
            f"no consumer probe `{name}` for CMake target `{library}`: the project does not "
            f"enable the language, or `{library}` is not a static or shared library"
        )
    edges = target_edges(graph, name)
    compile_edges = [e for e in edges if is_compile_edge(e)]
    link_edges = [e for e in edges if is_link_edge(e)]
    if len(compile_edges) != 1 or len(link_edges) != 1:
        raise TestBuildError(f"consumer probe `{name}` has an unexpected shape: {edges}")
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
        raise TestBuildError(f"cannot tell the link language of `{link_edge.rule}`")

    def tokens(name: str) -> list[str]:
        return shlex.split(rebase(graph.lookup(link_edge, name), relative_root))

    # CMake >= 3.27 asks the linker for a dependency file of the probe's own link; not ours.
    flags = [t for t in tokens("FLAGS") + tokens("LINK_FLAGS") if not t.startswith(_LINK_DEPFILE)]
    return ConsumerLink(
        driver=driver,
        flags=tuple(flags),
        libraries=tuple(tokens("LINK_PATH") + tokens("LINK_LIBRARIES")),
    )


def merge_flags(first: tuple[str, ...], *others: tuple[str, ...]) -> tuple[str, ...]:
    """The first library's flags, then whatever the others add (their own usage requirements;
    the global flags are the same for every probe)."""
    merged = list(first)
    seen = set(first)
    for flags in others:
        for token in flags:
            if token not in seen:
                merged.append(token)
                seen.add(token)
    return tuple(merged)


@dataclass(frozen=True)
class CompileUnitRequest:
    project: ConfiguredProject
    owner: str
    """The test address, for messages."""
    source: str
    """The translation unit, relative to the build root."""
    argv: tuple[str, ...]
    """The complete compile command, every path relative to the build directory."""
    object: str
    """The object file, relative to the build directory."""
    files: Digest
    """The test's own files, which the scan may find includes in."""
    file_paths: tuple[str, ...]


@dataclass(frozen=True)
class CompiledUnit:
    object: str
    """Relative to the build root."""
    digest: Digest


@rule(desc="Compile test translation unit", level=LogLevel.DEBUG)
async def compile_test_unit(request: CompileUnitRequest) -> CompiledUnit:
    project = request.project
    build_dir = project.build_dir
    scan = await scan_test_translation_unit(
        ScanTestUnitRequest(
            project, request.owner, request.argv, request.source, request.files, request.file_paths
        ),
        **implicitly(),
    )
    wanted = {*scan.headers, request.source}
    digests = [
        project.build_dir_digest,
        project.generated,
        await create_digest(CreateDigest([Directory(f"{build_dir}/{os.path.dirname(request.object)}")])),
    ]
    for digest, paths in (
        (project.sources, project.source_paths),
        (project.extra, project.extra_paths),
        (request.files, request.file_paths),
    ):
        subset = sorted(p for p in paths if p in wanted)
        if subset:
            digests.append(await digest_subset_to_digest(DigestSubset(digest, PathGlobs(subset))))
    input_digest = await merge_digests(MergeDigests(digests))
    outputs = [request.object]
    if project.coverage:
        outputs.append(f"{request.object[:-2]}.gcno")
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=request.argv,
                description=f"Compile {request.source} ({request.owner})",
                input_digest=input_digest,
                working_directory=build_dir,
                env=project.toolchain.env,
                output_files=tuple(outputs),
                level=LogLevel.DEBUG,
            )
        )
    )
    return CompiledUnit(
        f"{build_dir}/{request.object}", await add_prefix(AddPrefix(result.output_digest, build_dir))
    )


@dataclass(frozen=True)
class TestBuildRequest:
    address: Address


@dataclass(frozen=True)
class BuiltTest:
    project: ConfiguredProject
    executable: str
    """Relative to the build root, under the project's build directory."""
    runtime: Digest
    """The executable and the outputs of every library it links against, transitively."""
    data: Digest
    """The files of the dependencies, generated ones included, staged for the run."""


async def _libraries_in_declared_order(target: Target, address: Address) -> list[Target]:
    """The `cmake_target` dependencies, the explicitly listed ones first in BUILD order (Pants
    resolves a dependencies field sorted), then any inferred ones."""
    resolved = await resolve_targets(**implicitly(DependenciesRequest(target[CMakeTestDependenciesField])))
    libraries = {t.address: t for t in resolved if t.has_field(CMakeTargetProjectField)}
    declared = [v for v in (target[CMakeTestDependenciesField].value or ()) if not v.startswith("!")]
    addresses = await concurrently(
        parse_address(v, address.spec_path, f"the `dependencies` field of {address}") for v in declared
    )
    ordered: list[Target] = []
    for dependency in addresses:
        library = libraries.pop(dependency, None)
        if library is not None:
            ordered.append(library)
    return ordered + [libraries[a] for a in sorted(libraries)]


@rule(desc="Build CMake test")
async def build_test(request: TestBuildRequest, platform: Platform, known: KnownPlatforms) -> BuiltTest:
    address = request.address
    wrapped = await resolve_target(
        WrappedTargetRequest(address, description_of_origin="cmake_test"), **implicitly()
    )
    target = wrapped.target
    dependencies, library_targets = await concurrently(
        resolve_targets(**implicitly(DependenciesRequest(target[CMakeTestDependenciesField]))),
        _libraries_in_declared_order(target, address),
    )
    if not library_targets:
        raise TestBuildError(
            f"{address}: `dependencies` must name at least one `cmake_target` library: the test "
            "is compiled as a consumer of it and run from its project's build directory"
        )
    for t in library_targets:
        if known.host not in known.listed(t[PlatformsField]):
            raise TestBuildError(
                f"{address}: tests build for the host, {known.host.name}, but `{t.address}` does "
                "not list it in `platforms`"
            )
    resolved = await concurrently(
        resolve_cmake_target(CMakeTargetRequest(t.address, known.host), **implicitly())
        for t in library_targets
    )
    projects = sorted({str(r.project.address) for r in resolved})
    if len(projects) > 1:
        raise TestBuildError(
            f"{address}: the `cmake_target` dependencies belong to different projects: {projects}"
        )
    for r in resolved:
        if r.info.kind not in _LIBRARY_KINDS:
            raise TestBuildError(
                f"{address}: cannot link against CMake target `{r.name}` ({r.info.kind}); "
                "`dependencies` must name static or shared libraries"
            )
    project = resolved[0].project
    names = [r.name for r in resolved]
    graph, targets, relative_root = project.graph, project.targets, project.relative_root
    build_dir = project.build_dir

    sources, extra = await concurrently(
        determine_source_files(SourceFilesRequest([target[CMakeTestSourcesField]])),
        determine_source_files(
            SourceFilesRequest(
                [t.get(SourcesField) for t in dependencies if t.has_field(SourcesField)],
                for_sources_types=(FileSourceField, ResourceSourceField, CCSourceField),
                enable_codegen=True,
            )
        ),
    )
    error = overlap_error(address, sources.files, extra.files)
    if error:
        raise TestBuildError(error)
    files = await merge_digests(MergeDigests([sources.snapshot.digest, extra.snapshot.digest]))
    file_paths = tuple(sorted({*sources.files, *extra.files}))
    units = list(dict.fromkeys(p for p in (*sources.files, *extra.files) if is_translation_unit(p)))
    if not units:
        raise TestBuildError(f"{address}: no translation unit to compile in `sources` or `dependencies`")
    # Optional roots: a file outside every root needs no include directory.
    roots = await get_optional_source_roots(SourceRootsRequest.for_files(file_paths))
    root_dirs = sorted(
        {r.source_root.path for r in roots.path_to_optional_root.values() if r.source_root is not None}
    )

    def from_build_dir(path: str) -> str:
        return os.path.normpath(os.path.join(relative_root, path))

    spec_path = address.spec_path
    include_flags = dict.fromkeys(
        [
            *(
                f"-I{from_build_dir(os.path.normpath(os.path.join(spec_path, d)))}"
                for d in target[CMakeTestIncludeDirsField].value or ()
            ),
            *(f"-I{from_build_dir(d)}" for d in root_dirs),
        ]
    )
    own_flags = [
        *include_flags,
        *(f"-D{d}" for d in target[CMakeTestDefinesField].value or ()),
        *(target[CMakeTestCompilerArgsField].value or ()),
    ]
    languages = {language(u) for u in units}
    flags = {
        lang: merge_flags(
            *(consumer_compile_flags(graph, targets, relative_root, name, lang) for name in names)
        )
        for lang in languages
    }
    compilers = {C: project.toolchain.c_compiler.path, CXX: project.toolchain.cxx_compiler.path}
    # Without the address's parameters: a parametrization that does not change the build (one
    # that only changes how the test runs, say) then shares every compile and link process.
    output_dir = f"{OUTPUT_DIR}/{address.parametrize({}, replace=True).path_safe_spec}"

    compiled = await concurrently(
        compile_test_unit(
            CompileUnitRequest(
                project=project,
                owner=str(address),
                source=unit,
                argv=(
                    compilers[language(unit)],
                    *flags[language(unit)],
                    *own_flags,
                    "-o",
                    f"{output_dir}/obj/{unit}.o",
                    "-c",
                    from_build_dir(unit),
                ),
                object=f"{output_dir}/obj/{unit}.o",
                files=files,
                file_paths=file_paths,
            ),
            **implicitly(),
        )
        for unit in units
    )

    # The link line: each library's probe for the test's language, in declared order, the
    # first giving the flags; a library CMake links a C consumer of with the C++ driver forces
    # it for the test as well.
    probe_lang = CXX if CXX in languages else C
    links = [consumer_link(graph, targets, relative_root, name, probe_lang) for name in names]
    driver = CXX if probe_lang == CXX or any(link.driver == CXX for link in links) else C
    boundary = boundary_outputs(targets, "")
    link_args: list[str] = []
    to_build: dict[str, None] = {}
    shared_dirs: list[str] = []
    for token in dict.fromkeys(t for link in links for t in link.libraries):
        if token.startswith("-Wl,-rpath,"):
            continue  # CMake's build-tree rpath; replaced with an $ORIGIN-relative one below
        link_args.append(token)
        if token in boundary:
            to_build[boundary[token]] = None
        if token.endswith(_SHARED_SUFFIXES) and os.path.dirname(token) not in shared_dirs:
            shared_dirs.append(os.path.dirname(token))
    libraries = await concurrently(
        build_target(BuildTargetRequest(project, name), **implicitly()) for name in to_build
    )

    name = address.generated_name or address.target_name
    if name == ".":
        name = address.target_name
    executable = f"{output_dir}/bin/{name.replace('/', '_')}"
    origin = "@loader_path" if platform.is_macos else "$ORIGIN"
    rpaths = [
        f"-Wl,-rpath,{origin}/{os.path.relpath(d or '.', os.path.dirname(executable))}" for d in shared_dirs
    ]
    argv = (
        compilers[driver],
        *links[0].flags,
        *(from_build_dir(c.object) for c in compiled),
        *link_args,
        *rpaths,
        *(target[CMakeTestLinkerArgsField].value or ()),
        "-o",
        executable,
    )
    link_inputs = await merge_digests(
        MergeDigests(
            [
                project.build_dir_digest,
                await create_digest(CreateDigest([Directory(f"{build_dir}/{os.path.dirname(executable)}")])),
                *(c.digest for c in compiled),
                *(library.digest for library in libraries),
            ]
        )
    )
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=argv,
                description=f"Link {address}",
                input_digest=link_inputs,
                working_directory=build_dir,
                env=project.toolchain.env,
                output_files=(executable,),
                level=LogLevel.DEBUG,
            )
        )
    )
    digest = await add_prefix(AddPrefix(result.output_digest, build_dir))
    runtime = await merge_digests(MergeDigests([digest, *(library.runtime for library in libraries)]))
    return BuiltTest(
        project=project,
        executable=f"{build_dir}/{executable}",
        runtime=runtime,
        data=extra.snapshot.digest,
    )


def rules():
    return collect_rules()
