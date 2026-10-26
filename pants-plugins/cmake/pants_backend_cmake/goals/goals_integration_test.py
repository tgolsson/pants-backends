"""`package` and `test` on a small C project with the real CMake and the host compiler: the
libraries come from CMake, the tests are compiled and linked by Pants as consumers."""

from typing import Any

import pytest
from pants.core import target_types as core_target_types
from pants.core.goals import package as core_package
from pants.core.goals import test as core_test
from pants.core.goals.package import BuiltPackage
from pants.core.goals.test import TestResult
from pants.core.target_types import FilesGeneratorTarget, RelocatedFiles
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import CreateDigest, DigestContents, FileContent
from pants.engine.internals.parametrize import Parametrize
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.intrinsics import create_digest
from pants.engine.rules import QueryRule, rule
from pants.engine.target import Dependencies, DependenciesRequest, Tags, Target
from pants.engine.unions import UnionRule
from pants.testutil.rule_runner import RuleRunner
from pants.util.frozendict import FrozenDict

from pants_backend_cmake import register
from pants_backend_cmake.goals.package import CMakeTargetFieldSet
from pants_backend_cmake.goals.test import CMakeTestFieldSet, CMakeTestRequest
from pants_backend_cmake.goals.wrapper import CMakeTestWrapper, CMakeTestWrapperRequest
from pants_backend_cmake.target_types import CMakeTestSourcesField
from pants_backend_cmake.util_rules import executable

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
cmake_tests(
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

cmake_test(
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


class WrapTagged(CMakeTestWrapperRequest):
    @classmethod
    def is_applicable(cls, target: Target) -> bool:
        return "wrapped" in (target[Tags].value or ())


@rule
async def wrap_tagged(request: WrapTagged) -> CMakeTestWrapper:
    script = await create_digest(
        CreateDigest(
            [FileContent("run.sh", b'#!/bin/sh\necho "wrapped by $WRAPPER"\nexec "$@"\n', is_executable=True)]
        )
    )
    return CMakeTestWrapper(
        argv_prefix=("{chroot}/wrapper/run.sh",),
        immutable_input_digests=FrozenDict({"wrapper": script}),
        env=FrozenDict({"WRAPPER": "demo"}),
    )


class WrapTaggedToo(WrapTagged):
    """A second wrapper for the same tests, which is an error."""


@rule
async def wrap_tagged_too(request: WrapTaggedToo) -> CMakeTestWrapper:
    return CMakeTestWrapper()


class WrapBroken(CMakeTestWrapperRequest):
    @classmethod
    def is_applicable(cls, target: Target) -> bool:
        return "broken-wrapper" in (target[Tags].value or ())


@rule
async def wrap_broken(request: WrapBroken) -> CMakeTestWrapper:
    raise ValueError("no emulator today")


def _rule_runner(*extra_rules: object) -> RuleRunner:
    return RuleRunner(
        rules=[
            wrap_tagged,
            UnionRule(CMakeTestWrapperRequest, WrapTagged),
            *extra_rules,
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *core_target_types.rules(),
            *core_package.rules(),
            *core_test.rules(),
            *env_vars.rules(),
            *register.rules(),
            QueryRule(BuiltPackage, [CMakeTargetFieldSet]),
            QueryRule(TestResult, [CMakeTestRequest.Batch]),
            QueryRule(Addresses, [DependenciesRequest]),
            QueryRule(executable.BuiltTest, [executable.TestBuildRequest]),
        ],
        target_types=[*register.target_types(), FilesGeneratorTarget, RelocatedFiles],
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
    batch: Any = CMakeTestRequest.Batch("", (CMakeTestFieldSet.create(target),), None)
    return rule_runner.request(TestResult, [batch])


def _generated(name: str) -> Address:
    return Address("", target_name="tests", generated_name=name)


def test_package(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    library = _package(rule_runner, "demo")
    assert [a.relpath for a in library.artifacts] == ["build/libdemo.a"]
    assert {fc.path for fc in rule_runner.request(DigestContents, [library.digest])} == {"build/libdemo.a"}

    shared = _package(rule_runner, "shared")
    assert [a.relpath for a in shared.artifacts] == ["build/libshared.so"]


def test_generated_tests(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    generated = rule_runner.get_target(_generated("tests/pass"))
    # The directory's translation units plus every header under the generator's sources.
    assert generated[CMakeTestSourcesField].value == ("tests/pass/main.c", "tests/pass/helper.h")
    assert rule_runner.get_target(_generated("tests/failing"))[CMakeTestSourcesField].value == (
        "tests/failing/main.c",
        "tests/pass/helper.h",
    )


def test_unknown_override_key(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner, BUILD=BUILD.replace('"tests/failing": {"args": []}', '"nope": {"args": []}'))
    with pytest.raises(ExecutionError, match="names tests that are not generated: \\['nope'\\]"):
        rule_runner.get_target(_generated("tests/pass"))


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


def test_build_failure_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    broken = _test(rule_runner, _generated("tests/broken"))
    assert broken.exit_code == 1
    assert "not_declared_anywhere" in broken.stderr_bytes.decode()


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


def test_project_dependency_is_inferred(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    target = rule_runner.get_target(Address("", target_name="demo"))
    addresses = rule_runner.request(Addresses, [DependenciesRequest(target[Dependencies])])
    assert Address("", target_name="proj") in addresses


def test_wrapper(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner, BUILD=BUILD.replace('name="single",', 'name="single",\n    tags=["wrapped"],')
    )
    # The wrapper's script is mounted at `{chroot}/wrapper`, runs before the executable with
    # the wrapper's env, and the test's own result comes through.
    single = _test(rule_runner, Address("", target_name="single"))
    assert single.exit_code == 0, single.stderr_bytes.decode()
    assert "wrapped by demo" in single.stdout_bytes.decode()

    # A test no wrapper applies to runs as is.
    passing = _test(rule_runner, _generated("tests/pass"))
    assert passing.exit_code == 0, passing.stderr_bytes.decode()
    assert "wrapped" not in passing.stdout_bytes.decode()


def test_wrapper_problems_are_failed_tests() -> None:
    rule_runner = _rule_runner(
        wrap_tagged_too,
        UnionRule(CMakeTestWrapperRequest, WrapTaggedToo),
        wrap_broken,
        UnionRule(CMakeTestWrapperRequest, WrapBroken),
    )
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('name="single",', 'name="single",\n    tags=["wrapped"],')
        + 'cmake_test(name="broken", sources=["single.c", "extra/single.h"], dependencies=[":demo"],'
        ' include_dirs=["extra"], defines=["SINGLE_SCALE=2"], tags=["broken-wrapper"])\n',
    )
    # Two wrappers apply to `single`; the wrapper of `broken` cannot be set up. Each is that
    # test's failure, not the goal's.
    two = _test(rule_runner, Address("", target_name="single"))
    assert two.exit_code == 1
    assert "more than one test wrapper applies: ['WrapTagged', 'WrapTaggedToo']" in two.stderr_bytes.decode()
    broken = _test(rule_runner, Address("", target_name="broken"))
    assert broken.exit_code == 1
    assert "WrapBroken failed: no emulator today" in broken.stderr_bytes.decode()


def test_generated_data_files(rule_runner: RuleRunner) -> None:
    # A dependency that generates files (here `relocated_files`) is staged like a `files` one.
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('"dependencies": [":demo", ":data"]', '"dependencies": [":demo", ":moved"]')
        + 'files(name="raw", sources=["raw/*.txt"])\n'
        + 'relocated_files(name="moved", files_targets=[":raw"], src="raw", dest="data")\n',
        **{"raw/input.txt": "moved\n"},
    )
    with_data = _test(rule_runner, _generated("tests/data"))
    assert with_data.exit_code == 0, with_data.stderr_bytes.decode()
    assert "hello moved" in with_data.stdout_bytes.decode()


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
