"""A small project's compile steps, configured with the real (downloaded) CMake and the host
compiler."""

import os

from pants.core.util_rules import external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc
from pants_backend_cc.util_rules.compile import CompileSteps
from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.target_types import targets
from pants_backend_cmake.util_rules import compile, configure, toolchain
from pants_backend_cmake.util_rules.compile import CMakeCompileStepsFieldSet
from pants_backend_platforms import register as platforms
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS
from pants_backend_platforms.util_rules.platforms import TargetPlatform

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C CXX)
configure_file(gen.c.in gen.c)
add_library(demo STATIC src/lib.c src/api.cpp ${CMAKE_BINARY_DIR}/gen.c)
"""


def test_compile_steps() -> None:
    rule_runner = RuleRunner(
        rules=[
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *cc.rules(),
            *cmake.rules(),
            *ninja.rules(),
            *toolchain.rules(),
            *configure.rules(),
            *compile.rules(),
            QueryRule(CompileSteps, [CMakeCompileStepsFieldSet, TargetPlatform]),
        ],
        target_types=[*targets(), *platforms.target_types()],
    )
    rule_runner.write_files(
        {
            "BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "gen.c.in", "src/*"])\n',
            "CMakeLists.txt": CMAKELISTS,
            "gen.c.in": "int gen(void) { return 1; }\n",
            "src/lib.c": "int lib(void) { return 2; }\n",
            "src/api.cpp": "int api() { return 3; }\n",
        }
    )
    rule_runner.set_options([], env_inherit={"PATH"})
    field_set = CMakeCompileStepsFieldSet.create(rule_runner.get_target(Address("", target_name="proj")))
    steps = rule_runner.request(CompileSteps, [field_set, TargetPlatform(HOST, HOST_OS, HOST_ARCH)])

    by_source = {s.source: s for s in steps}
    assert set(by_source) == {"src/lib.c", "src/api.cpp", "build/gen.c"}
    assert [by_source[s].generated for s in ("src/lib.c", "src/api.cpp", "build/gen.c")] == [
        False,
        False,
        True,
    ]
    lib, api = by_source["src/lib.c"], by_source["src/api.cpp"]
    assert (lib.language, api.language, by_source["build/gen.c"].language) == ("c", "cxx", "c")
    assert lib.object == "build/CMakeFiles/demo.dir/src/lib.c.o"
    for step in steps:
        assert step.owner == Address("", target_name="proj")
        assert step.cwd == "build"
        assert "PATH" in step.env
        assert os.path.isabs(step.argv[0])
    # The implicit native toolchain's compilers.
    assert (os.path.basename(lib.argv[0]), os.path.basename(api.argv[0])) == ("gcc", "g++")
    assert "../src/lib.c" in lib.argv
