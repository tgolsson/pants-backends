"""`package` and `test` on a small C project with the real CMake and the host compiler: the
libraries come from CMake, and `cc_test`s consume them through their link interface."""

import os
import re
import shutil
import subprocess
from typing import Any

import pytest
from pants.backend.cc.target_types import CCSourcesGeneratorTarget
from pants.core import target_types as core_target_types
from pants.core.goals import package as core_package
from pants.core.goals import test as core_test
from pants.core.goals.package import BuiltPackage
from pants.core.goals.test import TestResult
from pants.core.target_types import FilesGeneratorTarget, RelocatedFiles
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import DigestContents
from pants.engine.internals.parametrize import Parametrize
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    HydratedSources,
    HydrateSourcesRequest,
)
from pants.source import source_root
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.goals.package import CcBinaryFieldSet
from pants_backend_cc.goals.test import CcTestFieldSet, CcTestRequest
from pants_backend_cc.testutil import GenTarget, codegen_rules
from pants_backend_cc.toy_library import ToyLibraryTarget, toy_library_rules
from pants_backend_cc.util_rules import executable
from pants_backend_cmake import register
from pants_backend_cmake.goals.package import CMakeTargetFieldSet
from pants_backend_cmake.util_rules.toolchain import CMakeToolchainFileField
from pants_backend_platforms import register as platforms_register
from pants_backend_platforms.testutil import HOST, OTHER, platforms_build

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C CXX)
add_library(base STATIC base.c)
add_library(demo STATIC lib.c)
target_include_directories(demo PUBLIC ${CMAKE_SOURCE_DIR}/include PRIVATE ${CMAKE_SOURCE_DIR}/private)
target_compile_definitions(demo PUBLIC DEMO_ANSWER=42 PRIVATE DEMO_PRIVATE=1)
target_link_libraries(demo PUBLIC base m)
add_library(shared SHARED shared.c)
target_include_directories(shared PUBLIC ${CMAKE_SOURCE_DIR}/include)
target_compile_definitions(shared PUBLIC SHARED_VALUE=7)
add_library(cxxlib STATIC cxxlib.cpp)
"""

BUILD = """\
cmake_project(name="proj", sources=["CMakeLists.txt", "*.c", "*.cpp", "include/*.h", "private/*.h"])
cmake_target(name="demo", project=":proj")
cmake_target(name="shared", project=":proj")
cmake_target(name="cxxlib", project=":proj")
files(name="framework", sources=["framework/*"])
files(name="data", sources=["data/*.txt"])

# One test per directory under tests/. The flags are what CMake gives a consumer of the
# libraries: `-I include` and `-DDEMO_ANSWER=42` from demo, `-DSHARED_VALUE=7` from shared,
# nothing PRIVATE; the link line brings `libbase.a` and `-lm` behind demo. The framework's
# check.c is compiled into every test; the C tests link a C++ library, so the link runs the
# C++ driver.
cc_tests(
    name="tests",
    sources=["tests/**/*.c", "tests/**/*.h"],
    dependencies=[":demo", ":shared", ":cxxlib", ":framework"],
    include_dirs=["framework"],
    args=["--verbose", "extra"],
    overrides={
        "tests/failing": {"args": []},
        "tests/data": {"dependencies": [":demo", ":data"], "extra_env_vars": ["DEMO_GREETING=hello"]},
    },
)

cc_test(
    name="single",
    sources=["single.c", "extra/single.h"],
    dependencies=[":demo"],
    include_dirs=["extra"],
    defines=["SINGLE_SCALE=2"],
    compiler_args=["-Wall", "-Werror"],
    linker_args=["-Wl,--as-needed"],
)
"""

FILES = {
    "BUILD": BUILD,
    "CMakeLists.txt": CMAKELISTS,
    "base.c": "int base_value(void) { return 5; }\n",
    "lib.c": '#include "secret.h"\n#ifndef DEMO_PRIVATE\n#error "the library itself must see its private define"\n#endif\n'  # noqa: E501
    "int demo(void) { return DEMO_ANSWER + SECRET - SECRET; }\n",
    "private/secret.h": "#define SECRET 9\n",
    "shared.c": "int shared_value(void) { return SHARED_VALUE; }\n",
    # A function-local static needs the C++ runtime (`__cxa_guard_*`) at link time.
    "cxxlib.cpp": "#include <string>\n"
    'extern "C" int cxx_value(void) { static std::string s("cxx"); return (int)s.size(); }\n',
    "include/demo.h": "int demo(void);\nint base_value(void);\nint shared_value(void);\nint cxx_value(void);\n",  # noqa: E501
    "framework/check.h": "int check(int actual, int expected);\n",
    "framework/check.c": '#include <stdio.h>\n#include "check.h"\n'
    "int check(int actual, int expected) { if (actual == expected) return 0; "
    'printf("expected %d, got %d\\n", expected, actual); return 1; }\n',
    "tests/pass/main.c": "#include <math.h>\n#include <stdio.h>\n"
    '#include "demo.h"\n#include "check.h"\n#include "helper.h"\n'
    '#ifdef DEMO_PRIVATE\n#error "a PRIVATE define of the library leaked into the test"\n#endif\n'
    'int main(int argc, char **argv) { printf("args=%d\\n", argc); '
    "return check(demo(), DEMO_ANSWER) + check(base_value(), 5) + check(shared_value(), SHARED_VALUE) "
    "+ check(cxx_value(), 3) + check(helper(), 1) + check((int)sqrt(16.0), 4); }\n",
    "tests/pass/helper.h": "static inline int helper(void) { return 1; }\n",
    "tests/failing/main.c": '#include <stdio.h>\nint main(void) { puts("nope"); return 3; }\n',
    "tests/data/main.c": "#include <stdio.h>\n#include <stdlib.h>\n"
    'int main(void) { FILE *f = fopen("../data/input.txt", "r"); if (!f) return 2; '
    'char buf[32] = {0}; fgets(buf, sizeof buf, f); printf("%s %s\\n", getenv("DEMO_GREETING"), buf); return 0; }\n',  # noqa: E501
    "tests/broken/main.c": "int main(void) { return not_declared_anywhere(); }\n",
    "data/input.txt": "world\n",
    "extra/single.h": "#define SINGLE_BASE 21\n",
    "single.c": '#include <math.h>\n#include "demo.h"\n#include "single.h"\n'
    "int main(void) { return (int)sqrt((double)(SINGLE_BASE * SINGLE_SCALE)) == 6 && demo() == 42 ? 0 : 1; }\n",  # noqa: E501
}


def _rule_runner(*extra_rules: object) -> RuleRunner:
    return RuleRunner(
        rules=[
            *extra_rules,
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *core_target_types.rules(),
            *core_package.rules(),
            *core_test.rules(),
            *env_vars.rules(),
            *source_root.rules(),
            *codegen_rules(),
            *register.rules(),
            QueryRule(BuiltPackage, [CMakeTargetFieldSet]),
            QueryRule(BuiltPackage, [CcBinaryFieldSet]),
            QueryRule(TestResult, [CcTestRequest.Batch]),
            QueryRule(Addresses, [DependenciesRequest]),
            QueryRule(executable.BuiltTest, [executable.TestBuildRequest]),
        ],
        target_types=[
            *register.target_types(),
            *platforms_register.target_types(),
            *cc_register.target_types(),
            CCSourcesGeneratorTarget,
            FilesGeneratorTarget,
            RelocatedFiles,
            GenTarget,
            ToyLibraryTarget,
        ],
        objects={"parametrize": Parametrize},
    )


@pytest.fixture
def rule_runner() -> RuleRunner:
    return _rule_runner()


def _write_project(rule_runner: RuleRunner, **overrides: str) -> None:
    rule_runner.write_files({**FILES, **overrides})
    rule_runner.set_options([], env_inherit={"PATH"})


def _package(rule_runner: RuleRunner, name: str) -> BuiltPackage:
    target = rule_runner.get_target(Address("", target_name=name))
    return rule_runner.request(BuiltPackage, [CMakeTargetFieldSet.create(target)])


def _test(rule_runner: RuleRunner, address: Address) -> TestResult:
    target = rule_runner.get_target(address)
    batch: Any = CcTestRequest.Batch("", (CcTestFieldSet.create(target),), None)
    return rule_runner.request(TestResult, [batch])


def _package_binary(rule_runner: RuleRunner, name: str) -> BuiltPackage:
    target = rule_runner.get_target(Address("", target_name=name))
    return rule_runner.request(BuiltPackage, [CcBinaryFieldSet.create(target)])


def _generated(name: str) -> Address:
    return Address("", target_name="tests", generated_name=name)


def test_package(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    library = _package(rule_runner, "demo")
    assert [a.relpath for a in library.artifacts] == ["build/libdemo.a"]
    assert {fc.path for fc in rule_runner.request(DigestContents, [library.digest])} == {"build/libdemo.a"}

    shared = _package(rule_runner, "shared")
    assert [a.relpath for a in shared.artifacts] == ["build/libshared.so"]


def test_run_tests(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    # Consumer flags (`-I include`, `-DDEMO_ANSWER=42`, `-DSHARED_VALUE=7`, no PRIVATE define),
    # the transitive `libbase.a` and `-lm` from demo's link interface, a framework file compiled
    # in, a header next to the test, a C++ library linked into a C test, and a shared library
    # found through the rpath at run time.
    passing = _test(rule_runner, _generated("tests/pass"))
    assert passing.exit_code == 0, passing.stdout_bytes.decode() + passing.stderr_bytes.decode()
    assert "args=3" in passing.stdout_bytes.decode()

    failing = _test(rule_runner, _generated("tests/failing"))
    assert failing.exit_code == 3
    assert "nope" in failing.stdout_bytes.decode()

    # Overridden `dependencies` stage the data files; `extra_env_vars` are passed.
    with_data = _test(rule_runner, _generated("tests/data"))
    assert with_data.exit_code == 0, with_data.stderr_bytes.decode()
    assert "hello world" in with_data.stdout_bytes.decode()


def test_missing_header_names_the_test_and_the_include(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner, BUILD=BUILD.replace('sources=["single.c", "extra/single.h"]', 'sources=["single.c"]')
    )
    result = _test(rule_runner, Address("", target_name="single"))
    assert result.exit_code == 1
    message = result.stderr_bytes.decode()
    assert message.startswith(
        "//:single: single.c includes files that none of its inputs provide: ['single.h']"
    )


def test_single_test_with_own_flags(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    # `include_dirs`, `defines`, `compiler_args` and `linker_args`; `-lm` comes from demo.
    single = _test(rule_runner, Address("", target_name="single"))
    assert single.exit_code == 0, single.stderr_bytes.decode()


def test_package_cc_binary(rule_runner: RuleRunner) -> None:
    # Built in the project's build directory against demo's link interface, then run from `dist/`.
    _write_project(
        rule_runner,
        BUILD=BUILD + 'cc_binary(name="app", sources=["single.c", "extra/single.h"], dependencies=[":demo"], '
        'include_dirs=["extra"], defines=["SINGLE_SCALE=2"])\n',
    )
    package = _package_binary(rule_runner, "app")
    contents = rule_runner.request(DigestContents, [package.digest])
    assert [(fc.path, fc.is_executable) for fc in contents] == [("app", True)]
    rule_runner.write_digest(package.digest, path_prefix="dist")
    result = subprocess.run([os.path.join(rule_runner.build_root, "dist", "app")], capture_output=True)
    assert result.returncode == 0, result.stderr


def test_project_dependency_is_inferred(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    target = rule_runner.get_target(Address("", target_name="demo"))
    addresses = rule_runner.request(Addresses, [DependenciesRequest(target[Dependencies])])
    assert Address("", target_name="proj") in addresses


def test_parametrizations_share_the_build(rule_runner: RuleRunner) -> None:
    # Parametrizations that differ only in how the test runs build the same executable at the
    # same path, so every compile and link process is shared.
    _write_project(
        rule_runner,
        BUILD=BUILD.replace(
            'name="single",', 'name="single",\n    **parametrize("a"),\n    **parametrize("b", args=["x"]),'
        ),
    )
    a, b = (
        rule_runner.request(
            executable.BuiltTest,
            [executable.TestBuildRequest(Address("", target_name="single", parameters={"parametrize": p}))],
        )
        for p in ("a", "b")
    )
    assert a.executable == b.executable == "build/__pants_tests/.single/bin/single"
    assert a.runtime == b.runtime


def test_package_compiles_generated_source(rule_runner: RuleRunner) -> None:
    # The placeholder configures the library; the replay must compile the generated bytes.
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('"private/*.h"])', '"private/*.h"], dependencies=[":gen"])')
        + 'test_gen(name="gen", sources=["gen/answer.cpp.in"])\n'
        + 'cmake_target(name="answer", project=":proj")\n'
        + 'cc_test(name="answer_test", sources=["answer_test.c"], dependencies=[":answer"])\n',
        **{
            "CMakeLists.txt": CMAKELISTS + "add_library(answer STATIC gen/answer.cpp)\n",
            "gen/answer.cpp.in": 'extern "C" int answer(void) { return 42; }\n',
            "answer_test.c": "int answer(void);\nint main(void) { return answer() == 42 ? 0 : 1; }\n",
        },
    )
    library = _package(rule_runner, "answer")
    assert [a.relpath for a in library.artifacts] == ["build/libanswer.a"]
    result = _test(rule_runner, Address("", target_name="answer_test"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_sources_from_cc_sources_dependencies(rule_runner: RuleRunner) -> None:
    # Every C file comes from `cc_sources`; the project's `sources` is only CMakeLists.txt.
    _write_project(
        rule_runner,
        BUILD=BUILD.replace(
            'sources=["CMakeLists.txt", "*.c", "*.cpp", "include/*.h", "private/*.h"])',
            'sources=["CMakeLists.txt"], dependencies=[":lib"])',
        )
        + 'cc_sources(name="lib", sources=["*.c", "*.cpp", "include/*.h", "private/*.h"])\n'
        + 'cc_sources(name="owned", sources=["owned/*.c"])\n'
        + 'cc_test(name="owned_test", dependencies=[":demo", ":owned"])\n',
        **{"owned/main.c": '#include "demo.h"\nint main(void) { return demo() == 42 ? 0 : 1; }\n'},
    )
    library = _package(rule_runner, "demo")
    assert [a.relpath for a in library.artifacts] == ["build/libdemo.a"]
    result = _test(rule_runner, Address("", target_name="owned_test"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


MINGW_CC = next(
    (cc for cc in ("x86_64-w64-mingw32-gcc-win32", "x86_64-w64-mingw32-gcc") if shutil.which(cc)), None
)
WINDOWS = OTHER
PLATFORMS_BUILD = (
    platforms_build()
    + f"""\
cc_toolchain(name="native", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", ar="ar")
cc_toolchain(
    name="mingw",
    host="{HOST}",
    target="{WINDOWS}",
    cc="{MINGW_CC}",
    cxx="{(MINGW_CC or "").replace("-gcc", "-g++")}",
    ar="x86_64-w64-mingw32-ar",
    cmake_toolchain_file="windows.cmake",
)
"""
)


def _with_platforms(build: str, platforms: str, *names: str) -> str:
    for name in names:
        build = build.replace(
            f'name="{name}", project=":proj"', f'name="{name}", project=":proj", {platforms}'
        )
    return build


@pytest.mark.skipif(MINGW_CC is None or not shutil.which("x86_64-w64-mingw32-ar"), reason="needs MinGW-w64")
def test_package_for_windows(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=_with_platforms(BUILD, f'platforms=["{HOST}", "{WINDOWS}"]', "demo", "shared"),
        **{
            "platforms/BUILD": PLATFORMS_BUILD,
            "platforms/windows.cmake": "set(CMAKE_SYSTEM_NAME Windows)\nset(CMAKE_SYSTEM_PROCESSOR x86_64)\n",
        },
    )
    rule_runner.set_options([f"--platforms-enabled=['{WINDOWS}']"], env_inherit={"PATH"})

    library = _package(rule_runner, "demo")
    assert [a.relpath for a in library.artifacts] == ["build/libdemo.a", f"{WINDOWS}/build/libdemo.a"]
    contents = {fc.path: fc.content for fc in rule_runner.request(DigestContents, [library.digest])}
    assert b"lib.c.obj" not in contents["build/libdemo.a"]
    assert b"lib.c.obj" in contents[f"{WINDOWS}/build/libdemo.a"]

    shared = _package(rule_runner, "shared")
    assert sorted(a.relpath for a in shared.artifacts) == [
        "build/libshared.so",
        f"{WINDOWS}/build/libshared.dll",
        f"{WINDOWS}/build/libshared.dll.a",
    ]
    contents = {fc.path: fc.content for fc in rule_runner.request(DigestContents, [shared.digest])}
    assert contents["build/libshared.so"].startswith(b"\x7fELF")
    assert contents[f"{WINDOWS}/build/libshared.dll"].startswith(b"MZ")


def test_toolchain_file_is_optional() -> None:
    rule_runner = _rule_runner(QueryRule(HydratedSources, [HydrateSourcesRequest]))
    _write_project(rule_runner, **{"platforms/BUILD": PLATFORMS_BUILD})
    native = rule_runner.get_target(Address("platforms", target_name="native"))
    hydrated = rule_runner.request(HydratedSources, [HydrateSourcesRequest(native[CMakeToolchainFileField])])
    assert hydrated.snapshot.files == ()


def test_cc_test_needs_the_host_platform(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=_with_platforms(BUILD, f'platforms=["{WINDOWS}"]', "demo")
        + 'cc_test(name="wintest", sources=["wintest.c"], dependencies=[":demo"])\n',
        **{
            "platforms/BUILD": platforms_build(),
            "wintest.c": "int main(void) { return 0; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="wintest"))
    assert result.exit_code == 1
    assert (
        f"executables build for the host, {HOST}, but `//:demo` does not list it"
        in result.stderr_bytes.decode()
    )


def test_cc_binary_needs_the_host_platform(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=_with_platforms(BUILD, f'platforms=["{WINDOWS}"]', "demo")
        + 'cc_binary(name="winapp", sources=["winapp.c"], dependencies=[":demo"])\n',
        **{
            "platforms/BUILD": platforms_build(),
            "winapp.c": "int main(void) { return 0; }\n",
        },
    )
    message = f"//:winapp: executables build for the host, {HOST}, but `//:demo` does not list it"
    with pytest.raises(ExecutionError, match=re.escape(message)):
        _package_binary(rule_runner, "winapp")


def test_cc_test_builds_for_the_host_only(rule_runner: RuleRunner) -> None:
    # No toolchain builds for Windows, so building the library for it would fail.
    _write_project(
        rule_runner,
        BUILD=_with_platforms(BUILD, f'platforms=["{HOST}", "{WINDOWS}"]', "demo")
        + 'cc_test(name="hosttest", sources=["hosttest.c"], dependencies=[":demo"])\n',
        **{
            "platforms/BUILD": platforms_build()
            + f'cc_toolchain(name="native", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", ar="ar")\n',
            "hosttest.c": '#include "demo.h"\nint main(void) { return demo() == DEMO_ANSWER ? 0 : 1; }\n',
        },
    )
    rule_runner.set_options([f"--platforms-enabled=['{WINDOWS}']"], env_inherit={"PATH"})
    result = _test(rule_runner, Address("", target_name="hosttest"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_cc_test_of_an_executable_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'cmake_target(name="tool", project=":proj")\n'
        + 'cc_test(name="of_tool", sources=["single.c", "extra/single.h"], dependencies=[":tool"])\n',
        **{
            "CMakeLists.txt": CMAKELISTS + "add_executable(tool tool.c)\n",
            "tool.c": "int main(void) { return 0; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="of_tool"))
    assert result.exit_code == 1
    assert (
        "//:of_tool: cannot link against CMake target `tool` of //:tool (EXECUTABLE)"
        in result.stderr_bytes.decode()
    )


# A second project, in another directory and so another build directory, enabling only C.
C_ONLY = {
    "c_only/BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"])\n'
    'cmake_target(name="c_only", project=":proj")\n',
    "c_only/CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(c_only LANGUAGES C)\n"
    "add_library(c_only STATIC c_only.c)\n",
    "c_only/c_only.c": "int c_only(void) { return 1; }\n",
}


def test_cc_test_in_a_language_the_project_lacks_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD + 'cc_test(name="cxx_on_c", sources=["cxx_on_c.cpp"], dependencies=["//c_only"])\n',
        **C_ONLY,
        **{"cxx_on_c.cpp": "int main() { return 0; }\n"},
    )
    result = _test(rule_runner, Address("", target_name="cxx_on_c"))
    assert result.exit_code == 1
    assert (
        "//:cxx_on_c: no consumer probe `__pants_probe_c_only_CXX` for CMake target `c_only`: its "
        "project does not enable the language" in result.stderr_bytes.decode()
    )


def test_coverage_build_writes_notes(rule_runner: RuleRunner) -> None:
    # The project's coverage flags reach the test's compiles through the consumer flags.
    flags = ", ".join(
        f'"-DCMAKE_{v}=--coverage"'
        for v in ("C_FLAGS", "CXX_FLAGS", "EXE_LINKER_FLAGS", "SHARED_LINKER_FLAGS")
    )
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('"private/*.h"])', f'"private/*.h"], coverage_cmake_args=[{flags}])'),
    )
    built = rule_runner.request(
        executable.BuiltTest, [executable.TestBuildRequest(Address("", target_name="single"), coverage=True)]
    )
    notes = rule_runner.request(DigestContents, [built.coverage_notes])
    assert [fc.path for fc in notes] == ["build/__pants_tests/.single/obj/single.c.gcno"]


def test_cc_test_links_a_cmake_and_another_library(rule_runner: RuleRunner) -> None:
    # The other backend's library takes the CMake build directory as its consumers' working
    # directory.
    rule_runner = _rule_runner(*toy_library_rules())
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="toy", sources=["toy/toy.c", "toy/toy.h"])\n'
        + 'cc_test(name="mixed", sources=["mixed.c"], dependencies=[":demo", ":toy"])\n',
        **{
            "toy/toy.c": "int toy(void) { return 5; }\n",
            "toy/toy.h": "int toy(void);\n",
            "mixed.c": '#include "demo.h"\n#include "toy/toy.h"\n'
            "int main(void) { return demo() + toy() == 47 ? 0 : 1; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="mixed"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_cc_test_of_two_projects_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'cc_test(name="two", sources=["single.c", "extra/single.h"], dependencies=[":demo", "//c_only"])\n',
        **C_ONLY,
    )
    result = _test(rule_runner, Address("", target_name="two"))
    assert result.exit_code == 1
    assert (
        "//:two: the libraries need different working directories: ['//:demo: build', "
        "'c_only:c_only: c_only/build']" in result.stderr_bytes.decode()
    )


def test_cc_test_compiles_each_language_with_its_flags(rule_runner: RuleRunner) -> None:
    # demo gives C++ consumers `-std=gnu++17` and a define that the C unit must not see.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'cc_test(name="mixed", sources=["mixed/main.cpp", "mixed/util.c"], dependencies=[":demo"])\n',
        **{
            "CMakeLists.txt": CMAKELISTS + "target_compile_features(demo PUBLIC cxx_std_17)\n"
            "target_compile_definitions(demo INTERFACE $<$<COMPILE_LANGUAGE:CXX>:DEMO_CXX=1>)\n",
            "mixed/main.cpp": "#if __cplusplus < 201703L || !defined(DEMO_CXX)\n"
            '#error "no C++ consumer flags"\n#endif\n'
            'extern "C" int util(void);\nint main() { return util(); }\n',
            "mixed/util.c": '#ifdef DEMO_CXX\n#error "C++ consumer flags"\n#endif\n'
            '#include "demo.h"\nint util(void) { return demo() == 42 ? 0 : 1; }\n',
        },
    )
    result = _test(rule_runner, Address("", target_name="mixed"))
    assert result.exit_code == 0, result.stderr_bytes.decode()
