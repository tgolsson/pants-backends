"""The tools every configure, scan and replay process runs with: the pinned CMake and Ninja
downloads, and the `cc_toolchain` (pants-backend-cc) for the platform being built, with
its `cmake_toolchain_file` when it has one."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, download_external_tool
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.core.util_rules.system_binaries import BinaryPath
from pants.engine.fs import EMPTY_DIGEST, Digest
from pants.engine.internals.graph import resolve_target
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import OptionalSingleSourceField, WrappedTargetRequest
from pants.util.frozendict import FrozenDict

from pants_backend_cc.target_types import CcToolchainTarget
from pants_backend_cc.util_rules.toolchain import (
    CcToolchain,
    CcToolchainRequest,
    resolve_cc_toolchain,
)
from pants_backend_cmake.subsystems.cmake import CMakeTool
from pants_backend_cmake.subsystems.ninja import NinjaTool
from pants_backend_platforms.util_rules.platforms import TargetPlatform

CMAKE_DIR = "__cmake"
NINJA_DIR = "__ninja"


class CMakeToolchainFileField(OptionalSingleSourceField):
    alias = "cmake_toolchain_file"
    expected_file_extensions = (".cmake",)
    help = (
        "A CMake toolchain file for this toolchain's target, such as one setting "
        "`CMAKE_SYSTEM_NAME`. It must not set the compilers or `ar`, which come from this "
        "`cc_toolchain`, and configure gets this file alone, not files it `include()`s."
    )


@dataclass(frozen=True)
class CMakeToolchainRequest:
    platform: TargetPlatform


@dataclass(frozen=True)
class CMakeToolchain:
    cmake: DownloadedExternalTool
    ninja: DownloadedExternalTool
    cc: CcToolchain
    toolchain_file: str | None
    """Relative to the build root; in `toolchain_file_digest`."""
    toolchain_file_digest: Digest

    @property
    def c_compiler(self) -> BinaryPath:
        return self.cc.cc

    @property
    def cxx_compiler(self) -> BinaryPath:
        return self.cc.cxx

    @property
    def immutable_input_digests(self) -> FrozenDict[str, Digest]:
        return FrozenDict({CMAKE_DIR: self.cmake.digest, NINJA_DIR: self.ninja.digest})

    @property
    def cmake_exe(self) -> str:
        return f"{CMAKE_DIR}/{self.cmake.exe}"

    @property
    def cmake_bin_dir(self) -> str:
        return os.path.dirname(self.cmake_exe)

    @property
    def ninja_exe(self) -> str:
        return os.path.normpath(f"{NINJA_DIR}/{self.ninja.exe}")

    @property
    def env(self) -> FrozenDict[str, str]:
        return self.cc.env


@rule(desc="Resolve the CMake toolchain")
async def resolve_cmake_toolchain(
    request: CMakeToolchainRequest, cmake: CMakeTool, ninja: NinjaTool, platform: Platform
) -> CMakeToolchain:
    cmake_tool, ninja_tool, cc = await concurrently(
        download_external_tool(cmake.get_request(platform)),
        download_external_tool(ninja.get_request(platform)),
        resolve_cc_toolchain(CcToolchainRequest(request.platform), **implicitly()),
    )
    toolchain_file, toolchain_file_digest = None, EMPTY_DIGEST
    if cc.address is not None:
        wrapped = await resolve_target(
            WrappedTargetRequest(cc.address, description_of_origin="cmake toolchain"), **implicitly()
        )
        field = wrapped.target[CMakeToolchainFileField]
        if field.value:
            files = await determine_source_files(SourceFilesRequest([field]))
            toolchain_file, toolchain_file_digest = files.files[0], files.snapshot.digest
    return CMakeToolchain(cmake_tool, ninja_tool, cc, toolchain_file, toolchain_file_digest)


def rules():
    return [*collect_rules(), CcToolchainTarget.register_plugin_field(CMakeToolchainFileField)]
