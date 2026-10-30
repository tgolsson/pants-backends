"""Configure a `cmake_project` once, in a sandbox, into a tree whose digest does not depend on
the sandbox.

Configure sees the CMake files, and every other configure-time input, as they are, and a
zero-byte placeholder at every path matching `configure_placeholders` (sources and headers):
CMake only checks that those exist, so editing one never reruns configure, only adding or
removing one does. Matching files of `dependencies`, generated ones included, are placeholders
too; configure does not see their other files. The consumer probes (`probes.py`) are injected
through `CMAKE_PROJECT_INCLUDE`. From the result the generated build files are kept and canonicalized
(see `canonicalize.py`); the cache, the compiler-id artefacts, the logs under `CMakeFiles/`
and the probe sources are dropped so they cannot break digest identity.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pants.backend.cc.target_types import CCSourceField
from pants.core.target_types import FileSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import (
    CreateDigest,
    Digest,
    DigestSubset,
    Directory,
    FileContent,
    MergeDigests,
    PathGlobs,
)
from pants.engine.internals.graph import resolve_target, resolve_targets
from pants.engine.intrinsics import (
    create_digest,
    digest_subset_to_digest,
    get_digest_contents,
    merge_digests,
)
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import DependenciesRequest, SourcesField, WrappedTargetRequest
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_cc.util_rules.fields import overlap_error
from pants_backend_cmake.ninja.graph import CMakeTargetInfo, discover_targets
from pants_backend_cmake.ninja.parser import NinjaGraph, parse_ninja
from pants_backend_cmake.target_types import (
    CMakeArgsField,
    CMakeBuildDirField,
    CMakeCoverageArgsField,
    CMakeProjectDependenciesField,
    CMakeProjectPlaceholdersField,
    CMakeProjectSourcesField,
)
from pants_backend_cmake.util_rules.canonicalize import (
    canonicalize,
    canonicalize_build_ninja,
    sandbox_root,
    tool_paths,
)
from pants_backend_cmake.util_rules.probes import PROBES_DIR, PROBES_SCRIPT, PROBES_SCRIPT_NAME
from pants_backend_cmake.util_rules.toolchain import (
    CMakeToolchain,
    CMakeToolchainRequest,
    resolve_cmake_toolchain,
)
from pants_backend_platforms.util_rules.platforms import TargetPlatform


@dataclass(frozen=True)
class ConfigureRequest:
    address: Address
    platform: TargetPlatform
    build_type: str | None = None
    """`CMAKE_BUILD_TYPE`; `None` is CMake's default."""
    coverage: bool = False
    """The build for coverage consumers, with `coverage_cmake_args`."""


def generated_by(address: Address, generator: Address) -> bool:
    """Whether `generator` generated `address`, parameters included:
    `maybe_convert_to_target_generator()` drops them, and a parametrized project's targets carry
    them."""
    return address.is_generated_target and (address.spec_path, address.target_name, address.parameters) == (
        generator.spec_path,
        generator.target_name,
        generator.parameters,
    )


@dataclass(frozen=True)
class ConfiguredProject:
    address: Address
    project_dir: str
    """Directory of the project's BUILD file, relative to the build root ("" at the root)."""
    build_dir: str
    """The CMake build directory, relative to the build root."""
    build_dir_digest: Digest
    """Just the empty build directory, so a process can run in it."""
    tree: Digest
    """The canonicalized generated files, under `build_dir`."""
    generated: Digest
    """The subset of `tree` the build reads: configure-time outputs such as `configure_file`
    results, without the Ninja files and the compile database."""
    generated_paths: tuple[str, ...]
    graph: NinjaGraph
    targets: FrozenDict[str, CMakeTargetInfo]
    sources: Digest
    source_paths: tuple[str, ...]
    extra: Digest
    """Files of `dependencies`, generated ones included, mounted next to the sources."""
    extra_paths: tuple[str, ...]
    toolchain: CMakeToolchain
    coverage: bool
    """Configured with `coverage_cmake_args`: compiles write `.gcno` notes."""

    @property
    def relative_root(self) -> str:
        """The build root as seen from `build_dir`, what the placeholder rebases to."""
        return os.path.relpath(".", self.build_dir)


def build_dir_path(address: Address, value: str) -> str:
    """`build_dir`, relative to the build root. It must be a subdirectory of the project: the
    canonicalized tree is rebased from it, and the configure writes only below it."""
    relative = os.path.normpath(value) if value else ""
    if not relative or os.path.isabs(relative) or relative == "." or relative.split(os.sep)[0] == "..":
        raise ValueError(
            f"{address}: `build_dir` must be a subdirectory of the project's directory, not `{value}`"
        )
    return os.path.join(address.spec_path, relative)


def matches_any(relative_path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatchcase(relative_path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(relative_path, pattern[3:]):
            return True
    return False


_USED_TOOL_RE = re.compile(r'^set\((CMAKE_C_COMPILER|CMAKE_CXX_COMPILER|CMAKE_AR) "([^"]*)"\)$', re.MULTILINE)


def tool_mismatches(compiler_files: Iterable[str], expected: Mapping[str, str]) -> list[str]:
    """The tools CMake recorded in `CMakeFiles/<version>/CMake<LANG>Compiler.cmake` that are not
    the expected ones: a toolchain file's plain `set()` of a compiler shadows the `-D` cache entry."""
    used = {m.group(1): m.group(2) for text in compiler_files for m in _USED_TOOL_RE.finditer(text)}
    return [
        f"{var} is `{used[var]}`, not `{path}`"
        for var, path in expected.items()
        if used.get(var, path) != path
    ]


def configure_process(
    address: Address,
    toolchain: CMakeToolchain,
    project_dir: str,
    build_dir: str,
    probes_path: str,
    input_digest: Digest,
    build_type: str | None,
    cmake_args: tuple[str, ...],
    description: str | None = None,
    **kwargs,
) -> Process:
    argv = [
        toolchain.cmake_exe,
        "-S",
        project_dir or ".",
        "-B",
        build_dir,
        "-G",
        "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={{chroot}}/{toolchain.ninja_exe}",
        f"-DCMAKE_C_COMPILER={toolchain.c_compiler.path}",
        f"-DCMAKE_CXX_COMPILER={toolchain.cxx_compiler.path}",
        f"-DCMAKE_AR={toolchain.cc.ar.path}",
        *(
            [f"-DCMAKE_TOOLCHAIN_FILE={{chroot}}/{toolchain.toolchain_file}"]
            if toolchain.toolchain_file
            else []
        ),
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        f"-DCMAKE_PROJECT_INCLUDE={{chroot}}/{probes_path}",
        *([f"-DCMAKE_BUILD_TYPE={build_type}"] if build_type else []),
        *cmake_args,
    ]
    return Process(
        argv=argv,
        description=description or f"Configure {address}",
        input_digest=input_digest,
        immutable_input_digests=toolchain.immutable_input_digests,
        env=toolchain.env,
        output_directories=(build_dir,),
        level=LogLevel.DEBUG,
        **kwargs,
    )


def split_placeholders(
    address: Address,
    source_files: tuple[str, ...],
    extra_files: tuple[str, ...],
    patterns: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """(real files, placeholders): matching `sources` and matching dependency files become
    placeholders."""
    prefix = f"{address.spec_path}/" if address.spec_path else ""

    def relative(path: str) -> str:
        return path[len(prefix) :] if path.startswith(prefix) else path

    real_files = []
    placeholders = []
    for path in source_files:
        (placeholders if matches_any(relative(path), patterns) else real_files).append(path)
    # A generated TU named in `add_library` must exist at configure.
    placeholders.extend(p for p in extra_files if matches_any(relative(p), patterns))
    if not real_files:
        raise ValueError(
            f"{address}: every source matches `configure_placeholders`; configure needs "
            "at least a CMakeLists.txt"
        )
    return real_files, placeholders


async def configure_inputs(
    sources_digest: Digest,
    real_files: list[str],
    placeholders: list[str],
    build_dir: str,
    toolchain: CMakeToolchain,
) -> tuple[Digest, Digest, str]:
    """(input digest, build dir digest, probes path)."""
    probes_path = f"{build_dir}/{PROBES_DIR}/{PROBES_SCRIPT_NAME}"
    real_digest, placeholder_digest, build_dir_digest, probes_digest = await concurrently(
        digest_subset_to_digest(DigestSubset(sources_digest, PathGlobs(real_files))),
        create_digest(CreateDigest([FileContent(path, b"") for path in placeholders])),
        create_digest(CreateDigest([Directory(build_dir)])),
        create_digest(CreateDigest([FileContent(probes_path, PROBES_SCRIPT.encode())])),
    )
    input_digest = await merge_digests(
        MergeDigests([real_digest, placeholder_digest, probes_digest, toolchain.toolchain_file_digest])
    )
    return input_digest, build_dir_digest, probes_path


@dataclass(frozen=True)
class CanonicalTree:
    tree: Digest
    generated: Digest
    generated_paths: tuple[str, ...]
    graph: NinjaGraph


async def canonical_tree(
    address: Address, build_dir: str, output_digest: Digest, toolchain: CMakeToolchain
) -> CanonicalTree:
    # Everything generated, except the cache, the compiler-id artefacts and the logs; the
    # cache is read once for the tool paths it records and then dropped.
    generated, rules_ninja, cache, compiler_files = await concurrently(
        digest_subset_to_digest(
            DigestSubset(
                output_digest,
                PathGlobs(
                    [
                        f"{build_dir}/**",
                        f"!{build_dir}/CMakeFiles/**",
                        f"!{build_dir}/{PROBES_DIR}/**",
                        f"!{build_dir}/CMakeCache.txt",
                        f"!{build_dir}/cmake_install.cmake",
                        f"!{build_dir}/**/CTestTestfile.cmake",
                    ]
                ),
            )
        ),
        digest_subset_to_digest(
            DigestSubset(output_digest, PathGlobs([f"{build_dir}/CMakeFiles/rules.ninja"]))
        ),
        digest_subset_to_digest(DigestSubset(output_digest, PathGlobs([f"{build_dir}/CMakeCache.txt"]))),
        digest_subset_to_digest(
            DigestSubset(output_digest, PathGlobs([f"{build_dir}/CMakeFiles/*/CMake*Compiler.cmake"]))
        ),
    )
    mismatches = tool_mismatches(
        (fc.content.decode(errors="replace") for fc in await get_digest_contents(compiler_files)),
        {
            "CMAKE_C_COMPILER": toolchain.c_compiler.path,
            "CMAKE_CXX_COMPILER": toolchain.cxx_compiler.path,
            "CMAKE_AR": toolchain.cc.ar.path,
        },
    )
    if mismatches:
        raise ValueError(
            f"{address}: configure did not use the `cc_toolchain`'s tools: "
            f"{'; '.join(mismatches)}. A `cmake_toolchain_file` must not set the compilers or `ar`."
        )
    kept = await merge_digests(MergeDigests([generated, rules_ninja]))
    contents, cache_contents = await concurrently(get_digest_contents(kept), get_digest_contents(cache))
    if not cache_contents:
        raise ValueError(f"{address}: configure produced no CMakeCache.txt")
    tools = tool_paths(cache_contents[0].content.decode(errors="replace"))

    raw = {fc.path: fc.content for fc in contents}
    build_ninja_path = f"{build_dir}/build.ninja"
    if build_ninja_path not in raw:
        raise ValueError(f"{address}: configure produced no {build_ninja_path}")
    root = sandbox_root(raw[build_ninja_path].decode(), build_dir)
    canonical: dict[str, bytes] = {}
    for path, content in raw.items():
        try:
            text = content.decode()
        except UnicodeDecodeError:
            canonical[path] = content
            continue
        if path == build_ninja_path:
            text = canonicalize_build_ninja(text, root, tools)
        else:
            text = canonicalize(text, root, tools)
        canonical[path] = text.encode()
    build_files = {
        build_ninja_path,
        f"{build_dir}/CMakeFiles/rules.ninja",
        f"{build_dir}/compile_commands.json",
    }
    tree, generated = await concurrently(
        create_digest(
            CreateDigest([FileContent(path, content) for path, content in sorted(canonical.items())])
        ),
        create_digest(
            CreateDigest(
                [
                    FileContent(path, content)
                    for path, content in sorted(canonical.items())
                    if path not in build_files
                ]
            )
        ),
    )
    generated_paths = tuple(sorted(path for path in canonical if path not in build_files))
    ninja_files = {
        path[len(build_dir) + 1 :]: content.decode()
        for path, content in canonical.items()
        if path.endswith(".ninja")
    }
    return CanonicalTree(tree, generated, generated_paths, parse_ninja(ninja_files))


@rule(desc="Configure CMake project", level=LogLevel.DEBUG)
async def configure_project(request: ConfigureRequest) -> ConfiguredProject:
    toolchain = await resolve_cmake_toolchain(CMakeToolchainRequest(request.platform), **implicitly())
    wrapped = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="cmake configure"), **implicitly()
    )
    target = wrapped.target
    resolved = await resolve_targets(**implicitly(DependenciesRequest(target[CMakeProjectDependenciesField])))
    # A generator depends on what it generates; those are the project's own handles, not inputs.
    dependencies = [t for t in resolved if not generated_by(t.address, request.address)]
    sources, extra = await concurrently(
        determine_source_files(SourceFilesRequest([target[CMakeProjectSourcesField]])),
        determine_source_files(
            SourceFilesRequest(
                [t.get(SourcesField) for t in dependencies if t.has_field(SourcesField)],
                for_sources_types=(FileSourceField, CCSourceField),
                enable_codegen=True,
            )
        ),
    )

    project_dir = request.address.spec_path
    error = overlap_error(request.address, sources.files, extra.files)
    if error:
        raise ValueError(error)

    patterns = tuple(target[CMakeProjectPlaceholdersField].value or ())
    real_files, placeholders = split_placeholders(request.address, sources.files, extra.files, patterns)
    build_dir = build_dir_path(request.address, target[CMakeBuildDirField].value)
    input_digest, build_dir_digest, probes_path = await configure_inputs(
        sources.snapshot.digest, real_files, placeholders, build_dir, toolchain
    )
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            configure_process(
                request.address,
                toolchain,
                project_dir,
                build_dir,
                probes_path,
                input_digest,
                request.build_type,
                (
                    *(target[CMakeArgsField].value or ()),
                    *(target[CMakeCoverageArgsField].value if request.coverage else ()),
                ),
                description=f"Configure {request.address} for coverage" if request.coverage else None,
            )
        )
    )
    canonical = await canonical_tree(request.address, build_dir, result.output_digest, toolchain)
    return ConfiguredProject(
        address=request.address,
        project_dir=project_dir,
        build_dir=build_dir,
        build_dir_digest=build_dir_digest,
        tree=canonical.tree,
        generated=canonical.generated,
        generated_paths=canonical.generated_paths,
        graph=canonical.graph,
        targets=discover_targets(canonical.graph),
        sources=sources.snapshot.digest,
        source_paths=sources.files,
        extra=extra.snapshot.digest,
        extra_paths=extra.files,
        toolchain=toolchain,
        coverage=request.coverage,
    )


def rules():
    return collect_rules()
