"""Configures a small C project with the real (downloaded) CMake and the host compiler."""

import pytest
from pants.core.util_rules import external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.target_types import targets
from pants_backend_cmake.testutil import GenTarget, codegen_rules
from pants_backend_cmake.util_rules import configure, toolchain
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER
from pants_backend_cmake.util_rules.configure import ConfiguredProject, ConfigureRequest

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C)
file(READ VERSION DEMO_VERSION)
string(STRIP "${DEMO_VERSION}" DEMO_VERSION)
configure_file(config.h.in config.h)
add_library(demo STATIC lib.c)
target_include_directories(demo PUBLIC ${CMAKE_SOURCE_DIR}/include ${CMAKE_BINARY_DIR})
add_executable(demo_test main.c)
target_link_libraries(demo_test PRIVATE demo)
"""


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *cmake.rules(),
            *ninja.rules(),
            *toolchain.rules(),
            *configure.rules(),
            *codegen_rules(),
            QueryRule(ConfiguredProject, [ConfigureRequest]),
        ],
        target_types=[*targets(), GenTarget],
    )


def test_configure_small_project(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "VERSION", "*.in", "*.c", "include/*.h"])\n',  # noqa: E501
            "CMakeLists.txt": CMAKELISTS,
            "VERSION": "1.2.3\n",
            "config.h.in": '#define DEMO_VERSION "@DEMO_VERSION@"\n',
            "lib.c": "int demo(void) { return 42; }\n",
            "include/demo.h": "int demo(void);\n",
            "main.c": '#include "demo.h"\n#include "config.h"\nint main(void) { return demo() == 42 ? 0 : 1; }\n',  # noqa: E501
        }
    )
    rule_runner.set_options([], env_inherit={"PATH"})
    configured = rule_runner.request(ConfiguredProject, [ConfigureRequest(Address("", target_name="proj"))])

    assert configured.project_dir == ""
    assert configured.build_dir == "build"
    assert configured.relative_root == ".."
    assert configured.targets["demo"].kind == "STATIC_LIBRARY"
    assert configured.targets["demo"].final_outputs == ("libdemo.a",)
    assert configured.targets["demo_test"].kind == "EXECUTABLE"
    assert configured.targets["demo_test"].final_outputs == ("demo_test",)
    assert set(configured.source_paths) == {
        "CMakeLists.txt",
        "VERSION",
        "config.h.in",
        "lib.c",
        "include/demo.h",
        "main.c",
    }

    contents = rule_runner.request(DigestContents, [configured.tree])
    by_path = {fc.path: fc.content.decode() for fc in contents}
    assert set(by_path) == {
        "build/build.ninja",
        "build/CMakeFiles/rules.ninja",
        "build/compile_commands.json",
        "build/config.h",
    }
    # Configure-time inputs were real files, not placeholders.
    assert by_path["build/config.h"] == '#define DEMO_VERSION "1.2.3"\n'
    assert configured.generated_paths == ("build/config.h",)
    for path, text in by_path.items():
        assert "pants-sandbox" not in text, path
        assert "/tmp/" not in text, path
        assert "immutable_inputs" not in text, path
    build_ninja = by_path["build/build.ninja"]
    assert "RERUN_CMAKE" not in build_ninja
    assert f"cmake_ninja_workdir = {PLACEHOLDER}/build/" in build_ninja

    edge = configured.graph.producers["CMakeFiles/demo.dir/lib.c.o"]
    assert edge.target == "demo"
    assert edge.inputs == (f"{PLACEHOLDER}/lib.c",)
    assert f"-I{PLACEHOLDER}/include" in configured.graph.command(edge)
    assert configured.graph.command(edge).startswith(configured.toolchain.c_compiler.path)
    assert len(configured.toolchain.binutils) == 3


# A project in a subdirectory whose library compiles a translation unit a codegen dependency
# generates from `gen/answer.cpp.in`.
GENERATED_BUILD = """\
cmake_project(name="proj", sources=["CMakeLists.txt", "lib.c"], dependencies=[":gen"])
test_gen(name="gen", sources=["gen/answer.cpp.in"])
"""

GENERATED_FILES = {
    "proj/BUILD": GENERATED_BUILD,
    "proj/CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(demo LANGUAGES C CXX)\n"
    "add_library(demo STATIC lib.c gen/answer.cpp)\n",
    "proj/lib.c": "int demo(void) { return 1; }\n",
    "proj/gen/answer.cpp.in": 'extern "C" int answer(void) { return 42; }\n',
}


def test_configure_with_generated_source(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(GENERATED_FILES)
    rule_runner.set_options([], env_inherit={"PATH"})
    configured = rule_runner.request(
        ConfiguredProject, [ConfigureRequest(Address("proj", target_name="proj"))]
    )

    # The generated file exists at configure (a placeholder) and keeps its content for the build.
    assert "proj/gen/answer.cpp" in configured.extra_paths
    assert "proj/gen/answer.cpp" not in configured.source_paths
    edge = configured.graph.producers["CMakeFiles/demo.dir/gen/answer.cpp.o"]
    assert edge.target == "demo"
    assert edge.inputs == (f"{PLACEHOLDER}/proj/gen/answer.cpp",)
    extra = rule_runner.request(DigestContents, [configured.extra])
    assert {fc.path: fc.content for fc in extra}["proj/gen/answer.cpp"].startswith(b'extern "C"')


def test_sources_overlapping_generated_file_is_an_error(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            **GENERATED_FILES,
            "proj/BUILD": GENERATED_BUILD.replace(
                'sources=["CMakeLists.txt", "lib.c"]', 'sources=["CMakeLists.txt", "**/*.c", "**/*.cpp"]'
            ),
            "proj/gen/answer.cpp": "stale committed copy\n",
        }
    )
    rule_runner.set_options([], env_inherit={"PATH"})
    with pytest.raises(ExecutionError) as error:
        rule_runner.request(ConfiguredProject, [ConfigureRequest(Address("proj", target_name="proj"))])
    message = str(error.value)
    assert "proj:proj: `sources` and a dependency both provide ['proj/gen/answer.cpp']" in message
    assert '"!gen/answer.cpp"' in message
