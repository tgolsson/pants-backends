"""Configures a small C project with the real (downloaded) CMake and the host compiler."""

import os

import pytest
from pants.core.util_rules import external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc
from pants_backend_cc.testutil import GenTarget, codegen_rules
from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.target_types import targets
from pants_backend_cmake.util_rules import configure, toolchain
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER
from pants_backend_cmake.util_rules.configure import ConfiguredProject, ConfigureRequest
from pants_backend_platforms import register as platforms
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS
from pants_backend_platforms.util_rules.platforms import TargetPlatform

HOST_PLATFORM = TargetPlatform(HOST, HOST_OS, HOST_ARCH)

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
            *cc.rules(),
            *cmake.rules(),
            *ninja.rules(),
            *toolchain.rules(),
            *configure.rules(),
            *codegen_rules(),
            QueryRule(ConfiguredProject, [ConfigureRequest]),
        ],
        target_types=[*targets(), *platforms.target_types(), *cc.target_types(), GenTarget],
    )


def test_configure_small_project(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "VERSION", "*.in", "*.c", "include/*.h"])\n',  # noqa: E501
            "CMakeLists.txt": CMAKELISTS,
            "VERSION": "1.2.3\n",
            "config.h.in": (
                '#define DEMO_VERSION "@DEMO_VERSION@"\n#define DEMO_DATA "@CMAKE_SOURCE_DIR@/data"\n'
            ),
            "lib.c": "int demo(void) { return 42; }\n",
            "include/demo.h": "int demo(void);\n",
            "main.c": '#include "demo.h"\n#include "config.h"\nint main(void) { return demo() == 42 ? 0 : 1; }\n',  # noqa: E501
        }
    )
    rule_runner.set_options([], env_inherit={"PATH"})
    configured = rule_runner.request(
        ConfiguredProject, [ConfigureRequest(Address("", target_name="proj"), HOST_PLATFORM)]
    )

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
    assert by_path["build/config.h"] == (
        f'#define DEMO_VERSION "1.2.3"\n#define DEMO_DATA "{PLACEHOLDER}/data"\n'
    )
    assert configured.generated_paths == ("build/config.h",)
    # The build reads it with the placeholder rebased, as its commands are.
    generated = rule_runner.request(DigestContents, [configured.generated])
    assert [fc.content.decode() for fc in generated] == [
        '#define DEMO_VERSION "1.2.3"\n#define DEMO_DATA "../data"\n'
    ]
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


TOOLCHAIN_FILES = {
    "BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "t.h.in", "t.c"])\n'
    f'platform(name="{HOST}", os="{HOST_OS}", arch="{HOST_ARCH}")\n'
    f'cc_toolchain(name="native", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", ar="ar", '
    'cmake_toolchain_file="native.cmake")\n',
    "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(t LANGUAGES C)\n"
    "configure_file(t.h.in t.h)\nadd_library(t STATIC t.c)\n",
    "t.h.in": '#define FROM_TOOLCHAIN "@FROM_TOOLCHAIN@"\n',
    "t.c": "int t(void) { return 0; }\n",
}


def _configure_with_toolchain_file(rule_runner: RuleRunner, toolchain_file: str) -> dict[str, str]:
    rule_runner.write_files({**TOOLCHAIN_FILES, "native.cmake": toolchain_file})
    rule_runner.set_options([], env_inherit={"PATH"})
    configured = rule_runner.request(
        ConfiguredProject, [ConfigureRequest(Address("", target_name="proj"), HOST_PLATFORM)]
    )
    return {
        fc.path: fc.content.decode() for fc in rule_runner.request(DigestContents, [configured.generated])
    }


def test_toolchain_file(rule_runner: RuleRunner) -> None:
    generated = _configure_with_toolchain_file(rule_runner, 'set(FROM_TOOLCHAIN "one")\n')
    assert generated["build/t.h"] == '#define FROM_TOOLCHAIN "one"\n'
    generated = _configure_with_toolchain_file(rule_runner, 'set(FROM_TOOLCHAIN "two")\n')
    assert generated["build/t.h"] == '#define FROM_TOOLCHAIN "two"\n'


@pytest.mark.skipif(not os.path.exists("/usr/bin/cc"), reason="needs a second C compiler path")
def test_toolchain_file_must_not_set_the_compiler(rule_runner: RuleRunner) -> None:
    with pytest.raises(
        ExecutionError,
        match=r"configure did not use the `cc_toolchain`'s tools: CMAKE_C_COMPILER is `/usr/bin/cc`",
    ):
        _configure_with_toolchain_file(rule_runner, "set(CMAKE_C_COMPILER /usr/bin/cc)\n")


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
        ConfiguredProject, [ConfigureRequest(Address("proj", target_name="proj"), HOST_PLATFORM)]
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
        rule_runner.request(
            ConfiguredProject, [ConfigureRequest(Address("proj", target_name="proj"), HOST_PLATFORM)]
        )
    message = str(error.value)
    assert "proj:proj: `sources` and a dependency both provide ['proj/gen/answer.cpp']" in message
    assert '"!gen/answer.cpp"' in message
