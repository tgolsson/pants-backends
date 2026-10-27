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

from pants_backend_cmake.ninja.graph import CMakeTargetInfo, discover_targets
from pants_backend_cmake.ninja.parser import NinjaGraph, parse_ninja
from pants_backend_cmake.target_types import (
    CMakeArgsField,
    CMakeBuildDirField,
    CMakeBuildTypeField,
    CMakeCoverageField,
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
from pants_backend_cmake.util_rules.toolchain import CMakeToolchain


@dataclass(frozen=True)
class ConfigureRequest:
    address: Address


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

    @property
    def relative_root(self) -> str:
        """The build root as seen from `build_dir`, what the placeholder rebases to."""
        return os.path.relpath(".", self.build_dir)


def matches_any(relative_path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatchcase(relative_path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(relative_path, pattern[3:]):
            return True
    return False


def overlap_error(address: Address, sources: tuple[str, ...], extra: tuple[str, ...]) -> str | None:
    """Two copies of one path must never reach a compile: which one won would depend on merge
    order."""
    overlap = sorted(set(sources) & set(extra))
    if not overlap:
        return None
    first = overlap[0]
    prefix = f"{address.spec_path}/" if address.spec_path else ""
    relative = first[len(prefix) :] if first.startswith(prefix) else first
    return (
        f"{address}: `sources` and a dependency both provide {overlap}; drop it from one of them, "
        f'e.g. "!{relative}" in `sources`'
    )


@rule(desc="Configure CMake project", level=LogLevel.DEBUG)
async def configure_project(request: ConfigureRequest, toolchain: CMakeToolchain) -> ConfiguredProject:
    wrapped = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="cmake configure"), **implicitly()
    )
    target = wrapped.target
    dependencies = await resolve_targets(
        **implicitly(DependenciesRequest(target[CMakeProjectDependenciesField]))
    )
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
    prefix = f"{project_dir}/" if project_dir else ""

    def relative(path: str) -> str:
        return path[len(prefix) :] if path.startswith(prefix) else path

    error = overlap_error(request.address, sources.files, extra.files)
    if error:
        raise ValueError(error)

    patterns = tuple(target[CMakeProjectPlaceholdersField].value or ())
    real_files = []
    placeholders = []
    for path in sources.files:
        (placeholders if matches_any(relative(path), patterns) else real_files).append(path)
    # A generated TU named in `add_library` must exist at configure.
    placeholders.extend(p for p in extra.files if matches_any(relative(p), patterns))
    if not real_files:
        raise ValueError(
            f"{request.address}: every source matches `configure_placeholders`; configure needs "
            "at least a CMakeLists.txt"
        )

    build_dir = os.path.normpath(os.path.join(project_dir, target[CMakeBuildDirField].value))
    probes_path = f"{build_dir}/{PROBES_DIR}/{PROBES_SCRIPT_NAME}"
    real_digest, placeholder_digest, build_dir_digest, probes_digest = await concurrently(
        digest_subset_to_digest(DigestSubset(sources.snapshot.digest, PathGlobs(real_files))),
        create_digest(CreateDigest([FileContent(path, b"") for path in placeholders])),
        create_digest(CreateDigest([Directory(build_dir)])),
        create_digest(CreateDigest([FileContent(probes_path, PROBES_SCRIPT.encode())])),
    )
    input_digest = await merge_digests(MergeDigests([real_digest, placeholder_digest, probes_digest]))

    build_type = target[CMakeBuildTypeField].value
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
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        f"-DCMAKE_PROJECT_INCLUDE={{chroot}}/{probes_path}",
        *([f"-DCMAKE_BUILD_TYPE={build_type}"] if build_type else []),
        *(target[CMakeArgsField].value or ()),
    ]
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=argv,
                description=f"Configure {request.address}",
                input_digest=input_digest,
                immutable_input_digests=toolchain.immutable_input_digests,
                env=toolchain.env,
                output_directories=(build_dir,),
                level=LogLevel.DEBUG,
            )
        )
    )

    # Everything generated, except the cache, the compiler-id artefacts and the logs; the
    # cache is read once for the tool paths it records and then dropped.
    generated, rules_ninja, cache = await concurrently(
        digest_subset_to_digest(
            DigestSubset(
                result.output_digest,
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
            DigestSubset(result.output_digest, PathGlobs([f"{build_dir}/CMakeFiles/rules.ninja"]))
        ),
        digest_subset_to_digest(
            DigestSubset(result.output_digest, PathGlobs([f"{build_dir}/CMakeCache.txt"]))
        ),
    )
    kept = await merge_digests(MergeDigests([generated, rules_ninja]))
    contents, cache_contents = await concurrently(get_digest_contents(kept), get_digest_contents(cache))
    if not cache_contents:
        raise ValueError(f"{request.address}: configure produced no CMakeCache.txt")
    tools = tool_paths(cache_contents[0].content.decode(errors="replace"))

    raw = {fc.path: fc.content for fc in contents}
    build_ninja_path = f"{build_dir}/build.ninja"
    if build_ninja_path not in raw:
        raise ValueError(f"{request.address}: configure produced no {build_ninja_path}")
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
    graph = parse_ninja(ninja_files)
    return ConfiguredProject(
        address=request.address,
        project_dir=project_dir,
        build_dir=build_dir,
        build_dir_digest=build_dir_digest,
        tree=tree,
        generated=generated,
        generated_paths=generated_paths,
        graph=graph,
        targets=discover_targets(graph),
        sources=sources.snapshot.digest,
        source_paths=sources.files,
        extra=extra.snapshot.digest,
        extra_paths=extra.files,
        toolchain=toolchain,
        coverage=bool(target[CMakeCoverageField].value),
    )


def rules():
    return collect_rules()
