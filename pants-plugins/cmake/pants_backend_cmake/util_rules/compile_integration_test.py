"""A small project's compile steps, configured with the real (downloaded) CMake and the host
compiler."""

import os

from pants.engine.addresses import Address
from pants.engine.rules import QueryRule

from pants_backend_cc.util_rules.compile import CompileSteps
from pants_backend_cmake.testutil import cmake_rule_runner
from pants_backend_cmake.util_rules.compile import CMakeCompileStepsFieldSet
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS
from pants_backend_platforms.util_rules.platforms import TargetPlatform

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C CXX)
configure_file(gen.c.in gen.c)
add_library(demo STATIC src/lib.c src/api.cpp ${CMAKE_BINARY_DIR}/gen.c)
"""


def test_compile_steps() -> None:
    rule_runner = cmake_rule_runner(QueryRule(CompileSteps, [CMakeCompileStepsFieldSet, TargetPlatform]))
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
    inputs = rule_runner.get_target(Address("", target_name="proj", generated_name="_inputs"))
    field_set = CMakeCompileStepsFieldSet.create(inputs)
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
