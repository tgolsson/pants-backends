"""`coverage_cmake_args`: the build a coverage consumer links, with the real CMake and the host
compiler, through to a gcovr report from the default lockfile."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals import test as core_test
from pants.core.goals.test import CoverageReports, FilesystemCoverageReport, TestResult
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import Digest, DigestContents, DigestSubset, MergeDigests, PathGlobs
from pants.engine.internals.parametrize import Parametrize
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.source import source_root
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.coverage import register as coverage_register
from pants_backend_cc.coverage.data import CcCoverageDataCollection
from pants_backend_cc.goals.test import CcTestFieldSet, CcTestRequest
from pants_backend_cc.testutil import implicit_toolchain_is_gcc
from pants_backend_cc.util_rules import executable
from pants_backend_cmake import register as cmake_register
from pants_backend_platforms import register as platforms_register

pytestmark = pytest.mark.skipif(not implicit_toolchain_is_gcc(), reason="the implicit toolchain must be gcc")

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C)
add_library(base STATIC src/base.c)
add_library(demo STATIC src/demo.c)
target_include_directories(demo PUBLIC ${CMAKE_SOURCE_DIR}/include)
target_link_libraries(demo PUBLIC base)
"""

COVERAGE_ARGS = '["-DCMAKE_C_FLAGS=--coverage", "-DCMAKE_EXE_LINKER_FLAGS=--coverage"]'

BUILD = """\
cmake_project(name="proj", sources=["CMakeLists.txt", "src/*", "include/*"]{coverage})
cmake_target(name="demo", project=":proj")
cc_test(name="t", sources=["t.c"], dependencies=[":demo"])
"""

FILES = {
    "CMakeLists.txt": CMAKELISTS,
    "include/demo.h": "int demo(int x);\nint base(void);\n",
    "src/demo.c": '#include "demo.h"\nint demo(int x) {\n  if (x > 0)\n    return base();\n  return 0;\n}\n',
    "src/base.c": "int base(void) {\n  return 42;\n}\n",
    "t.c": '#include "demo.h"\nint main(void) { return demo(1) == 42 ? 0 : 1; }\n',
}

ENV = {"PATH", "PYENV_ROOT", "HOME"}
TEST = Address("", target_name="t")
NOTES = [
    "build/CMakeFiles/base.dir/src/base.c.gcno",
    "build/CMakeFiles/demo.dir/src/demo.c.gcno",
    "build/__pants_tests/.t/obj/t.c.gcno",
]


def _rule_runner(*, coverage: bool, build: str | None = None) -> PythonRuleRunner:
    rule_runner = PythonRuleRunner(
        rules=[
            *python_register.rules(),
            *core_target_types.rules(),
            *core_test.rules(),
            *env_vars.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *source_root.rules(),
            *system_binaries.rules(),
            *coverage_register.rules(),
            *cmake_register.rules(),
            QueryRule(TestResult, [CcTestRequest.Batch]),
            QueryRule(CoverageReports, [CcCoverageDataCollection]),
            QueryRule(executable.BuiltTest, [executable.TestBuildRequest]),
            QueryRule(Digest, [MergeDigests]),
            QueryRule(Digest, [DigestSubset]),
        ],
        target_types=[
            *cmake_register.target_types(),
            *platforms_register.target_types(),
            *cc_register.target_types(),
        ],
        objects={"parametrize": Parametrize},
    )
    rule_runner.write_files(
        {
            **FILES,
            "BUILD": build
            or BUILD.format(coverage=f", coverage_cmake_args={COVERAGE_ARGS}" if coverage else ""),
        }
    )
    rule_runner.set_options(["--test-use-coverage", "--gcovr-report=['json']"], env_inherit=ENV)
    return rule_runner


def _build(rule_runner: PythonRuleRunner, coverage: bool) -> executable.BuiltTest:
    return rule_runner.request(executable.BuiltTest, [executable.TestBuildRequest(TEST, coverage)])


def _paths(rule_runner: PythonRuleRunner, built: executable.BuiltTest) -> list[str]:
    return sorted(fc.path for fc in rule_runner.request(DigestContents, [built.coverage]))


def test_a_coverage_consumer_gets_the_coverage_build() -> None:
    rule_runner = _rule_runner(coverage=True)
    # The notes of both libraries the link line brings and of the test, which compiles with the
    # consumer flags of the coverage build, and the files those compiles read.
    paths = _paths(rule_runner, _build(rule_runner, coverage=True))
    assert [p for p in paths if p.endswith(".gcno")] == NOTES
    assert {"include/demo.h", "src/base.c", "src/demo.c", "t.c"} <= set(paths)

    # A consumer not asking for coverage builds exactly as from a project without the arguments.
    usual = _build(rule_runner, coverage=False)
    assert rule_runner.request(DigestContents, [usual.coverage_notes]) == DigestContents([])
    without = _rule_runner(coverage=False)
    assert usual.runtime == _build(without, coverage=False).runtime


def test_a_project_without_coverage_args_serves_its_usual_build() -> None:
    rule_runner = _rule_runner(coverage=False)
    built = _build(rule_runner, coverage=True)
    assert [p for p in _paths(rule_runner, built) if p.endswith(".gcno")] == []


def _run(rule_runner: PythonRuleRunner, address: Address) -> TestResult:
    batch: Any = CcTestRequest.Batch("", (CcTestFieldSet.create(rule_runner.get_target(address)),), None)
    result = rule_runner.request(TestResult, [batch])
    assert result.exit_code == 0, result.stderr_bytes.decode()
    return result


def _lines(rule_runner: PythonRuleRunner, results: list[TestResult]) -> dict[str, dict[int, int]]:
    data = [r.coverage_data for r in results]
    reports = rule_runner.request(CoverageReports, [CcCoverageDataCollection(data)])
    (report,) = reports.reports
    assert isinstance(report, FilesystemCoverageReport)
    (content,) = rule_runner.request(DigestContents, [report.result_snapshot.digest])
    return {
        f["file"]: {line["line_number"]: line["count"] for line in f["lines"]}
        for f in json.loads(content.content)["files"]
    }


def test_report_of_a_cmake_library() -> None:
    rule_runner = _rule_runner(coverage=True)
    lines = _lines(rule_runner, [_run(rule_runner, TEST)])
    assert lines["src/demo.c"] == {2: 1, 3: 1, 4: 1, 5: 0}
    assert lines["src/base.c"] == {1: 1, 2: 1}


def test_tests_of_two_configures_of_one_build_directory() -> None:
    # Each configure writes its own `config.h`, which demo.c reads, and its own notes into
    # `build/`. `Plain` is a build type without flags of its own, so neither build optimizes
    # the lines away.
    build = (
        'cmake_project(name="proj", sources=["CMakeLists.txt", "src/*", "include/*"], '
        f'build_type=parametrize(a="Debug", b="Plain"), coverage_cmake_args={COVERAGE_ARGS})\n'
        'cmake_target(name="demo_a", project=":proj@build_type=a", cmake_name="demo")\n'
        'cmake_target(name="demo_b", project=":proj@build_type=b", cmake_name="demo")\n'
        'cc_test(name="ta", sources=["t.c"], dependencies=[":demo_a"])\n'
        'cc_test(name="tb", sources=["t.c"], dependencies=[":demo_b"])\n'
    )
    rule_runner = _rule_runner(coverage=True, build=build)
    rule_runner.write_files(
        {
            "CMakeLists.txt": CMAKELISTS
            + "configure_file(src/config.h.in config.h)\n"
            + "target_include_directories(demo PRIVATE ${CMAKE_BINARY_DIR})\n",
            "src/config.h.in": '#define FLAVOUR "@CMAKE_BUILD_TYPE@"\n',
            "src/demo.c": FILES["src/demo.c"] + '#include "config.h"\n',
        }
    )
    results = [_run(rule_runner, Address("", target_name=n)) for n in ("ta", "tb")]
    # Their compile inputs, gcov data aside, cannot simply be merged.
    inputs = [
        rule_runner.request(
            Digest, [DigestSubset(r.coverage_data.digest, PathGlobs(["**", "!**/*.gcno", "!**/*.gcda"]))]
        )
        for r in results
    ]
    with pytest.raises(ExecutionError, match="config.h"):
        rule_runner.request(Digest, [MergeDigests(inputs)])
    lines = _lines(rule_runner, results)
    assert lines["src/demo.c"] == {2: 2, 3: 2, 4: 2, 5: 0}
    assert lines["src/base.c"] == {1: 2, 2: 2}
