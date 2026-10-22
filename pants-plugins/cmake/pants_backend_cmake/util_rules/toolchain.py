"""The tools every configure, scan and replay process runs with: the pinned CMake and Ninja
downloads and the host compilers and binutils. The `--version` output of each host tool is
fingerprinted into the process environment, so a host toolchain upgrade invalidates a
persistent cache. (A downloaded toolchain would put the tools in the key by digest instead;
that is a hermetic toolchain, not implemented yet.)"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, download_external_tool
from pants.core.util_rules.system_binaries import (
    BinaryPath,
    BinaryPathRequest,
    BinaryPathTest,
    SystemBinariesSubsystem,
    find_binary,
)
from pants.engine.fs import Digest
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.frozendict import FrozenDict

from pants_backend_cmake.subsystems.cmake import CMakeTool
from pants_backend_cmake.subsystems.ninja import NinjaTool

CMAKE_DIR = "__cmake"
NINJA_DIR = "__ninja"
_BINUTILS = ("ar", "ranlib", "ld")


@dataclass(frozen=True)
class CMakeToolchain:
    cmake: DownloadedExternalTool
    ninja: DownloadedExternalTool
    c_compiler: BinaryPath
    cxx_compiler: BinaryPath
    binutils: tuple[BinaryPath, ...]
    search_path: tuple[str, ...]

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
        """Shared by every process: the host search path (CMake's `find_program` and the
        replayed `ar`/`ranlib` need it) and the tool fingerprints."""
        return FrozenDict(
            {
                "PATH": os.pathsep.join(self.search_path),
                "__PANTS_CMAKE_C_COMPILER": self.c_compiler.fingerprint,
                "__PANTS_CMAKE_CXX_COMPILER": self.cxx_compiler.fingerprint,
                "__PANTS_CMAKE_BINUTILS": ",".join(b.fingerprint for b in self.binutils),
            }
        )


def _request(spec: str, search_path) -> BinaryPathRequest:
    if os.path.isabs(spec):
        return BinaryPathRequest(
            binary_name=os.path.basename(spec),
            search_path=[os.path.dirname(spec)],
            test=BinaryPathTest(args=["--version"]),
        )
    return BinaryPathRequest(
        binary_name=spec, search_path=search_path, test=BinaryPathTest(args=["--version"])
    )


@rule(desc="Resolve the CMake toolchain")
async def resolve_cmake_toolchain(
    cmake: CMakeTool,
    ninja: NinjaTool,
    platform: Platform,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> CMakeToolchain:
    search_path = system_binaries.system_binary_paths
    requests = [
        _request(cmake.c_compiler, search_path),
        _request(cmake.cxx_compiler, search_path),
        *(_request(name, search_path) for name in _BINUTILS),
    ]
    cmake_tool, ninja_tool, *found = await concurrently(
        download_external_tool(cmake.get_request(platform)),
        download_external_tool(ninja.get_request(platform)),
        *(find_binary(request, **implicitly()) for request in requests),
    )
    paths = [
        paths.first_path_or_raise(request, rationale="build CMake projects")
        for request, paths in zip(requests, found)
    ]
    return CMakeToolchain(
        cmake=cmake_tool,
        ninja=ninja_tool,
        c_compiler=paths[0],
        cxx_compiler=paths[1],
        binutils=tuple(paths[2:]),
        search_path=tuple(search_path),
    )


def rules():
    return collect_rules()
