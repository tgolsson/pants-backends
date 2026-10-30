"""Compile and link a `cc_test` or `cc_binary` in one working directory against the libraries in
its `dependencies`, which their backends build and describe (`library.py`). Every library's flags
and link arguments are concatenated in declared order, unchanged, because only the library's
backend knows which of its options may be merged or reordered.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pants.backend.cc.target_types import CCSourceField
from pants.core.target_types import FileSourceField, ResourceSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import (
    EMPTY_DIGEST,
    AddPrefix,
    CreateDigest,
    Digest,
    DigestSubset,
    Directory,
    FileEntry,
    MergeDigests,
    PathGlobs,
    SymlinkEntry,
)
from pants.engine.internals.graph import find_valid_field_sets, resolve_target, resolve_targets
from pants.engine.internals.native_engine import IntrinsicError
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_subset_to_digest,
    get_digest_entries,
    merge_digests,
)
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    DependenciesRequest,
    FieldSetsPerTargetRequest,
    SourcesField,
    Target,
    WrappedTargetRequest,
)
from pants.source.source_root import SourceRootsRequest, get_optional_source_roots
from pants.util.logging import LogLevel

from pants_backend_cc.target_types import (
    TRANSLATION_UNIT_SUFFIXES,
    CcTestCompilerArgsField,
    CcTestDefinesField,
    CcTestDependenciesField,
    CcTestIncludeDirsField,
    CcTestLinkerArgsField,
    CcTestSourcesField,
    is_translation_unit,
)
from pants_backend_cc.util_rules.compile import CXX, C
from pants_backend_cc.util_rules.fields import overlap_error, parse_address
from pants_backend_cc.util_rules.library import (
    CcConsumer,
    CcLibraryError,
    CcLibraryFieldSet,
    compile_interface,
    library_cwd,
    link_interface,
)
from pants_backend_cc.util_rules.scan import HeaderUniverse, scan_headers
from pants_backend_cc.util_rules.toolchain import CcToolchainRequest, resolve_cc_toolchain
from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform

_CXX_SUFFIXES = tuple(s for s in TRANSLATION_UNIT_SUFFIXES if s != ".c")
_COVERAGE_FLAGS = frozenset({"--coverage", "-coverage", "-ftest-coverage"})
OUTPUT_DIR = "__pants_tests"
BINARY_OUTPUT_DIR = "__pants_binaries"


class ExecutableBuildError(Exception):
    pass


def language(path: str) -> str:
    return CXX if path.endswith(_CXX_SUFFIXES) else C


def concatenated(*per_library: Iterable[str]) -> tuple[str, ...]:
    return tuple(token for tokens in per_library for token in tokens)


@dataclass(frozen=True)
class CompileUnitRequest:
    universe: HeaderUniverse
    """The libraries' headers, scanned in the compile's working directory and environment."""
    library_files: Digest
    """The libraries' files at `universe.paths`, subset to those the scan finds."""
    generated: Digest
    owner: str
    """The executable's address, for messages."""
    source: str
    """The translation unit, relative to the sandbox root."""
    argv: tuple[str, ...]
    """The complete compile command, every path relative to the working directory."""
    object: str
    """The object file, relative to the working directory."""
    files: Digest
    """The executable's own files, which the scan may find includes in."""
    file_paths: tuple[str, ...]


@dataclass(frozen=True)
class CompiledUnit:
    object: str
    """Relative to the sandbox root."""
    digest: Digest
    """The object and, for a coverage build, its `.gcno`, relative to the sandbox root."""


def _in_cwd(cwd: str, path: str) -> str:
    return os.path.join(cwd, path) if cwd else path


async def _under_cwd(cwd: str, digest: Digest) -> Digest:
    return await add_prefix(AddPrefix(digest, cwd)) if cwd else digest


@rule(desc="Compile C/C++ translation unit", level=LogLevel.DEBUG)
async def compile_unit(request: CompileUnitRequest) -> CompiledUnit:
    universe = request.universe
    cwd = universe.cwd
    headers = await scan_headers(
        universe,
        request.owner,
        request.argv,
        request.source,
        (request.files,),
        request.file_paths,
        (request.source,),
    )
    wanted = {*headers, request.source}
    digests = [
        request.generated,
        await create_digest(CreateDigest([Directory(_in_cwd(cwd, os.path.dirname(request.object)))])),
    ]
    for digest, paths in (
        (request.library_files, universe.paths),
        (request.files, request.file_paths),
    ):
        subset = sorted(p for p in paths if p in wanted)
        if subset:
            digests.append(await digest_subset_to_digest(DigestSubset(digest, PathGlobs(subset))))
    input_digest = await merge_digests(MergeDigests(digests))
    outputs = [request.object]
    if _COVERAGE_FLAGS.intersection(request.argv):
        outputs.append(f"{request.object[:-2]}.gcno")
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=request.argv,
                description=f"Compile {request.source} ({request.owner})",
                input_digest=input_digest,
                working_directory=cwd or None,
                env=universe.env,
                output_files=tuple(outputs),
                level=LogLevel.DEBUG,
            )
        )
    )
    return CompiledUnit(_in_cwd(cwd, request.object), await _under_cwd(cwd, result.output_digest))


@dataclass(frozen=True)
class ExecutableBuildRequest:
    address: Address
    output_dir: str
    """Where the objects and the executable go, relative to the working directory."""
    coverage: bool = False
    """Build against the libraries' instrumented builds (`CcConsumer.coverage`) and link the
    gcov runtime."""


@dataclass(frozen=True)
class TestBuildRequest:
    address: Address
    coverage: bool = False


@dataclass(frozen=True)
class BuiltExecutable:
    cwd: str
    """Where the executable runs, relative to the sandbox root."""
    executable: str
    """Relative to the sandbox root, under `cwd`."""
    runtime: Digest
    """The executable and what the libraries it links against need at run time."""
    data: Digest
    """The files of the dependencies, generated ones included, staged for the run."""
    coverage_notes: Digest
    """The `.gcno` files of a coverage build, which gcov reads with the data a run writes."""
    coverage: Digest
    """For a coverage build, what gcov reads besides the data a run writes: the libraries'
    `CcLinkInterface.coverage`, `coverage_notes` and the executable's own files."""


BuiltTest = BuiltExecutable


@dataclass(frozen=True)
class _Library:
    target: Target
    field_set: CcLibraryFieldSet


async def _libraries_in_declared_order(target: Target, dependencies: tuple[Target, ...]) -> list[_Library]:
    """The library dependencies, the explicitly listed ones first in BUILD order (Pants
    resolves a dependencies field sorted), then any inferred ones."""
    address = target.address
    per_target = await find_valid_field_sets(
        FieldSetsPerTargetRequest(CcLibraryFieldSet, dependencies), **implicitly()
    )
    libraries: dict[Address, _Library] = {}
    for tgt, field_sets in zip(dependencies, per_target.collection):
        if len(field_sets) > 1:
            kinds = sorted(type(fs).__name__ for fs in field_sets)
            raise ExecutableBuildError(f"{address}: {tgt.address} is a library several ways: {kinds}")
        if field_sets:
            libraries[tgt.address] = _Library(tgt, field_sets[0])
    declared = [v for v in (target[CcTestDependenciesField].value or ()) if not v.startswith("!")]
    addresses = await concurrently(
        parse_address(v, address.spec_path, f"the `dependencies` field of {address}") for v in declared
    )
    ordered: list[_Library] = []
    for dependency in addresses:
        library = libraries.pop(dependency, None)
        if library is not None:
            ordered.append(library)
    return ordered + [libraries[a] for a in sorted(libraries)]


async def _ask_libraries(address: Address, requests: Sequence[Any]) -> list[Any]:
    """One answer per library; a library that cannot serve the executable fails its build.
    `requests` is built eagerly, so each request binds the consumer it was made for."""
    try:
        return list(await concurrently(requests))
    except CcLibraryError as error:
        raise ExecutableBuildError(f"{address}: {error}") from error


def _path_kinds(entries: Iterable[Any]) -> dict[str, set[tuple]]:
    """What each path is in one digest: a file (by content and mode), a symlink or a directory,
    parents included, so a file at one library's path clashes with a directory at another's."""
    kinds: dict[str, set[tuple]] = defaultdict(set)
    for entry in entries:
        if isinstance(entry, FileEntry):
            kinds[entry.path].add(("file", entry.file_digest, entry.is_executable))
        elif isinstance(entry, SymlinkEntry):
            kinds[entry.path].add(("symlink", entry.target))
        else:
            kinds[entry.path].add(("directory",))
        parent = os.path.dirname(entry.path)
        while parent:
            kinds[parent].add(("directory",))
            parent = os.path.dirname(parent)
    return kinds


async def _merge_from_libraries(address: Address, what: str, owned: list[tuple[str, Digest]]) -> Digest:
    """Libraries' outputs must not overlap; when two put different things at one path, the build
    fails naming them."""
    try:
        return await merge_digests(MergeDigests(d for _, d in owned))
    except IntrinsicError as error:
        entries = await concurrently(get_digest_entries(d) for _, d in owned)
        writers: dict[str, dict[str, None]] = defaultdict(dict)
        kinds: dict[str, set[tuple]] = defaultdict(set)
        for (who, _), digest_entries in zip(owned, entries):
            for path, path_kinds in _path_kinds(digest_entries).items():
                writers[path][who] = None
                kinds[path] |= path_kinds
        clashes = sorted(p for p, k in kinds.items() if len(k) > 1)
        if not clashes:
            raise ExecutableBuildError(
                f"{address}: the libraries' {what} cannot be merged: {error}"
            ) from error
        raise ExecutableBuildError(
            f"{address}: the libraries {list(writers[clashes[0]])} put different {what} at `{clashes[0]}`; a "
            "library's outputs belong under paths unique to it"
        ) from error


@rule(desc="Build C/C++ executable")
async def build_executable(
    request: ExecutableBuildRequest, platform: Platform, known: KnownPlatforms
) -> BuiltExecutable:
    address = request.address
    host = known.host
    wrapped = await resolve_target(
        WrappedTargetRequest(address, description_of_origin="cc_test or cc_binary"), **implicitly()
    )
    target = wrapped.target
    dependencies = await resolve_targets(**implicitly(DependenciesRequest(target[CcTestDependenciesField])))
    libraries = await _libraries_in_declared_order(target, tuple(dependencies))
    if not libraries:
        raise ExecutableBuildError(
            f"{address}: `dependencies` must name at least one library: the executable is compiled "
            "as a consumer of it"
        )
    for library in libraries:
        if host not in known.listed(library.target.get(PlatformsField)):
            raise ExecutableBuildError(
                f"{address}: executables build for the host, {host.name}, but "
                f"`{library.target.address}` does not list it in `platforms`"
            )
    sources, extra = await concurrently(
        determine_source_files(SourceFilesRequest([target[CcTestSourcesField]])),
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
        raise ExecutableBuildError(error)
    files = await merge_digests(MergeDigests([sources.snapshot.digest, extra.snapshot.digest]))
    file_paths = tuple(sorted({*sources.files, *extra.files}))
    units = list(dict.fromkeys(p for p in (*sources.files, *extra.files) if is_translation_unit(p)))
    if not units:
        raise ExecutableBuildError(
            f"{address}: no translation unit to compile in `sources` or `dependencies`"
        )
    languages = sorted({language(u) for u in units})
    link_language = CXX if CXX in languages else C

    # Asked once with the link language; the answer holds for every language.
    asking = CcConsumer(link_language, None, request.coverage)
    toolchain, cwds = await concurrently(
        resolve_cc_toolchain(CcToolchainRequest(host), **implicitly()),
        _ask_libraries(
            address,
            [
                library_cwd(
                    **implicitly({lib.field_set: CcLibraryFieldSet, host: TargetPlatform, asking: CcConsumer})
                )
                for lib in libraries
            ],
        ),
    )
    required = {lib.target.address: c.path for lib, c in zip(libraries, cwds) if c.path is not None}
    if len(set(required.values())) > 1:
        found = sorted(f"{a}: {p or '.'}" for a, p in required.items())
        raise ExecutableBuildError(f"{address}: the libraries need different working directories: {found}")
    cwd = next(iter(required.values()), "")
    relative_root = os.path.relpath(".", cwd or ".")

    def from_cwd(path: str) -> str:
        return os.path.normpath(os.path.join(relative_root, path))

    output_dir = request.output_dir
    linker = CcConsumer(link_language, cwd, request.coverage)

    async def compile_units() -> list[CompiledUnit]:
        # Optional roots: a file outside every root needs no include directory.
        roots = await get_optional_source_roots(SourceRootsRequest.for_files(file_paths))
        root_dirs = sorted(
            {r.source_root.path for r in roots.path_to_optional_root.values() if r.source_root is not None}
        )
        spec_path = address.spec_path
        include_flags = dict.fromkeys(
            [
                *(
                    f"-I{from_cwd(os.path.normpath(os.path.join(spec_path, d)))}"
                    for d in target[CcTestIncludeDirsField].value or ()
                ),
                *(f"-I{from_cwd(d)}" for d in root_dirs),
            ]
        )
        own_flags = [
            *include_flags,
            *(f"-D{d}" for d in target[CcTestDefinesField].value or ()),
            *(target[CcTestCompilerArgsField].value or ()),
        ]
        consumers = [CcConsumer(lang, cwd, request.coverage) for lang in languages]
        per_language = await concurrently(
            _ask_libraries(
                address,
                [
                    compile_interface(
                        **implicitly(
                            {lib.field_set: CcLibraryFieldSet, host: TargetPlatform, consumer: CcConsumer}
                        )
                    )
                    for lib in libraries
                ],
            )
            for consumer in consumers
        )
        # A library's pieces may differ by language, so a clash names the language too.
        owned = [
            (f"{lib.target.address} ({lang})", i)
            for lang, interfaces in zip(languages, per_language)
            for lib, i in zip(libraries, interfaces)
        ]
        cwd_digest = await create_digest(CreateDigest([Directory(cwd)])) if cwd else EMPTY_DIGEST
        headers, library_files, generated = await concurrently(
            _merge_from_libraries(
                address,
                "headers",
                [*((who, i.headers) for who, i in owned), ("the working directory", cwd_digest)],
            ),
            _merge_from_libraries(address, "files", [(who, i.files) for who, i in owned]),
            _merge_from_libraries(address, "generated files", [(who, i.generated) for who, i in owned]),
        )
        universe = HeaderUniverse(
            headers,
            tuple(dict.fromkeys(path for _, i in owned for path in i.file_paths)),
            cwd,
            toolchain.env,
        )
        flags = {
            lang: concatenated(*(i.flags for i in interfaces))
            for lang, interfaces in zip(languages, per_language)
        }
        compilers = {C: toolchain.cc.path, CXX: toolchain.cxx.path}
        return list(
            await concurrently(
                compile_unit(
                    CompileUnitRequest(
                        universe=universe,
                        library_files=library_files,
                        generated=generated,
                        owner=str(address),
                        source=unit,
                        argv=(
                            compilers[language(unit)],
                            *flags[language(unit)],
                            *own_flags,
                            "-o",
                            f"{output_dir}/obj/{unit}.o",
                            "-c",
                            from_cwd(unit),
                        ),
                        object=f"{output_dir}/obj/{unit}.o",
                        files=files,
                        file_paths=file_paths,
                    ),
                    **implicitly(),
                )
                for unit in units
            )
        )

    # The libraries build while the executable compiles.
    compiled, links = await concurrently(
        compile_units(),
        _ask_libraries(
            address,
            [
                link_interface(
                    **implicitly({lib.field_set: CcLibraryFieldSet, host: TargetPlatform, linker: CcConsumer})
                )
                for lib in libraries
            ],
        ),
    )

    driver = CXX if link_language == CXX or any(link.driver == CXX for link in links) else C
    name = address.generated_name or address.target_name
    if name == ".":
        name = address.target_name
    executable = f"{output_dir}/bin/{name.replace('/', '_')}"
    origin = "@loader_path" if platform.is_macos else "$ORIGIN"
    runtime_dirs = dict.fromkeys(d for link in links for d in link.runtime_dirs)
    rpaths = [
        f"-Wl,-rpath,{origin}/{os.path.relpath(d or '.', os.path.dirname(executable))}" for d in runtime_dirs
    ]
    compilers = {C: toolchain.cc.path, CXX: toolchain.cxx.path}
    argv = (
        compilers[driver],
        *concatenated(*(link.flags for link in links)),
        *(from_cwd(c.object) for c in compiled),
        *concatenated(*(link.args for link in links)),
        *rpaths,
        *(["--coverage"] if request.coverage else []),
        *(target[CcTestLinkerArgsField].value or ()),
        "-o",
        executable,
    )
    libraries_digest, library_runtime = await concurrently(
        _merge_from_libraries(
            address,
            "libraries",
            [(str(lib.target.address), link.libraries) for lib, link in zip(libraries, links)],
        ),
        _merge_from_libraries(
            address,
            "run-time files",
            [(str(lib.target.address), link.runtime) for lib, link in zip(libraries, links)],
        ),
    )
    link_inputs = await merge_digests(
        MergeDigests(
            [
                await create_digest(CreateDigest([Directory(_in_cwd(cwd, os.path.dirname(executable)))])),
                *(c.digest for c in compiled),
                libraries_digest,
            ]
        )
    )
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=argv,
                description=f"Link {address}",
                input_digest=link_inputs,
                working_directory=cwd or None,
                env=toolchain.env,
                output_files=(executable,),
                level=LogLevel.DEBUG,
            )
        )
    )
    digest = await _under_cwd(cwd, result.output_digest)
    runtime, objects = await concurrently(
        merge_digests(MergeDigests([digest, library_runtime])),
        merge_digests(MergeDigests(c.digest for c in compiled)),
    )
    notes = await digest_subset_to_digest(DigestSubset(objects, PathGlobs(["**/*.gcno"])))
    coverage = EMPTY_DIGEST
    if request.coverage:
        coverage = await _merge_from_libraries(
            address,
            "coverage files",
            [
                *((str(lib.target.address), link.coverage) for lib, link in zip(libraries, links)),
                (str(address), await merge_digests(MergeDigests([notes, files]))),
            ],
        )
    return BuiltExecutable(
        cwd=cwd,
        executable=_in_cwd(cwd, executable),
        runtime=runtime,
        data=extra.snapshot.digest,
        coverage_notes=notes,
        coverage=coverage,
    )


@rule(desc="Build C/C++ test")
async def build_test(request: TestBuildRequest) -> BuiltTest:
    # Without the address's parameters: a parametrization that does not change the build (one
    # that only changes how the test runs, say) then shares every compile and link process.
    address = request.address
    output_dir = f"{OUTPUT_DIR}/{address.parametrize({}, replace=True).path_safe_spec}"
    return await build_executable(
        ExecutableBuildRequest(address, output_dir, request.coverage), **implicitly()
    )


def rules():
    return collect_rules()
