"""Configures a small C project with the real (downloaded) CMake and the host compiler."""

import pytest
from pants.core.util_rules import external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.target_types import targets
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
            QueryRule(ConfiguredProject, [ConfigureRequest]),
        ],
        target_types=targets(),
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
