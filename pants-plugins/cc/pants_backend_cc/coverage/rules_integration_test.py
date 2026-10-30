"""`cc_test` under `--use-coverage` against `toy_library`, reported with gcovr from the default
lockfile: the report depends on the link interface alone."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals import test as core_test
from pants.core.goals.test import (
    ConsoleCoverageReport,
    CoverageReports,
    FilesystemCoverageReport,
    TestResult,
)
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.source import source_root
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.coverage import register
from pants_backend_cc.coverage.data import CcCoverageData, CcCoverageDataCollection
from pants_backend_cc.goals.test import CcTestFieldSet, CcTestRequest
from pants_backend_cc.testutil import implicit_toolchain_is_gcc, toolchain_build
from pants_backend_cc.toy_library import ToyLibraryTarget, toy_library_rules
from pants_backend_platforms import register as platforms_register
from pants_backend_platforms.testutil import HOST, platforms_build

pytestmark = pytest.mark.skipif(not implicit_toolchain_is_gcc(), reason="the implicit toolchain must be gcc")

PICK = "int pick(int x) {\n  if (x > 0)\n    return 1;\n  return 2;\n}\nint unused(void) {\n  return 3;\n}\n"
MAIN = '#include "pick.h"\nint main(void) { return pick(1) == 1 ? 0 : 1; }\n'
# Fails when linked with the gcov runtime, which only instrumented objects bring.
PROBE = (
    "extern void __gcov_merge_add(void) __attribute__((weak));\n"
    '#include "pick.h"\n'
    "int main(void) { return pick(1) == 1 && !__gcov_merge_add ? 0 : 1; }\n"
)

FILES = {
    "BUILD": 'toy_library(name="pick", sources=["pick.c", "pick.h"])\n'
    'cc_test(name="one", sources=["one.c"], dependencies=[":pick"])\n'
    'cc_test(name="two", sources=["two.c"], dependencies=[":pick"])\n'
    'cc_test(name="probe", sources=["probe.c"], dependencies=[":pick"])\n',
    "pick.c": PICK,
    "pick.h": "int pick(int x);\nint unused(void);\n",
    "one.c": MAIN,
    "two.c": MAIN,
    "probe.c": PROBE,
    "so/BUILD": 'toy_library(name="pick", sources=["pick.c", "pick.h"], shared=True)\n'
    'cc_test(name="t", sources=["t.c"], dependencies=[":pick"])\n',
    "so/pick.c": PICK,
    "so/pick.h": "int pick(int x);\nint unused(void);\n",
    "so/t.c": MAIN,
}

ENV = {"PATH", "PYENV_ROOT", "HOME"}
TESTS = (Address("", target_name="one"), Address("", target_name="two"))


def _rule_runner(*options: str, backend: bool = True, use_coverage: bool = True) -> PythonRuleRunner:
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
            *(register.rules() if backend else cc_register.rules()),
            *toy_library_rules(),
            QueryRule(TestResult, [CcTestRequest.Batch]),
            *([QueryRule(CoverageReports, [CcCoverageDataCollection])] if backend else []),
        ],
        target_types=[*platforms_register.target_types(), *cc_register.target_types(), ToyLibraryTarget],
    )
    rule_runner.write_files(FILES)
    rule_runner.set_options(
        [
            *(["--test-use-coverage"] if use_coverage else []),
            *(["--gcovr-report=['json', 'console']"] if backend else []),
            *options,
        ],
        env_inherit=ENV,
    )
    return rule_runner


def _run(rule_runner: PythonRuleRunner, address: Address) -> TestResult:
    batch: Any = CcTestRequest.Batch("", (CcTestFieldSet.create(rule_runner.get_target(address)),), None)
    return rule_runner.request(TestResult, [batch])


def _test(rule_runner: PythonRuleRunner, address: Address) -> TestResult:
    result = _run(rule_runner, address)
    assert result.exit_code == 0, result.stderr_bytes.decode()
    return result


def _report(rule_runner: PythonRuleRunner, results: list[TestResult]) -> tuple[dict, str]:
    data = [r.coverage_data for r in results]
    assert all(isinstance(d, CcCoverageData) for d in data)
    reports = rule_runner.request(CoverageReports, [CcCoverageDataCollection(data)])
    (console,) = (r for r in reports.reports if isinstance(r, ConsoleCoverageReport))
    (written,) = (r for r in reports.reports if isinstance(r, FilesystemCoverageReport))
    assert str(written.report_file) == "dist/coverage/cc/coverage.json"
    (content,) = rule_runner.request(DigestContents, [written.result_snapshot.digest])
    return json.loads(content.content), console.report


def _counts(report: dict, name: str = "pick.c") -> tuple[dict[int, int], dict[str, int]]:
    (pick,) = report["files"]
    assert pick["file"] == name
    return (
        {line["line_number"]: line["count"] for line in pick["lines"]},
        {f["name"]: f["execution_count"] for f in pick["functions"]},
    )


def test_two_tests_sum_into_one_report() -> None:
    rule_runner = _rule_runner()
    report, console = _report(rule_runner, [_test(rule_runner, a) for a in TESTS])
    # The library's lines, each test's hits added; the tests' own sources are not instrumented.
    lines, functions = _counts(report)
    assert lines == {1: 2, 2: 2, 3: 2, 4: 0, 6: 0, 7: 0}
    assert functions == {"pick": 2, "unused": 0}
    assert "pick.c" in console


def test_a_shared_library() -> None:
    rule_runner = _rule_runner()
    report, _ = _report(rule_runner, [_test(rule_runner, Address("so", target_name="t"))])
    lines, functions = _counts(report, "so/pick.c")
    assert lines == {1: 1, 2: 1, 3: 1, 4: 0, 6: 0, 7: 0}
    assert functions == {"pick": 1, "unused": 0}


def test_a_cached_test_run_still_reports() -> None:
    _test(_rule_runner(), TESTS[0])
    # A runner of its own shares only the process cache, which keeps the run's outputs.
    rule_runner = _rule_runner()
    lines, _ = _counts(_report(rule_runner, [_test(rule_runner, TESTS[0])])[0])
    assert lines[3] == 1


@pytest.mark.parametrize(
    "backend, use_coverage",
    [
        pytest.param(True, True, id="coverage"),
        pytest.param(True, False, id="no-use-coverage"),
        pytest.param(False, True, id="no-coverage-backend"),
    ],
)
def test_only_a_coverage_run_builds_for_coverage(backend: bool, use_coverage: bool) -> None:
    result = _run(_rule_runner(backend=backend, use_coverage=use_coverage), Address("", target_name="probe"))
    coverage = backend and use_coverage
    assert result.exit_code == (1 if coverage else 0), result.stderr_bytes.decode()
    assert isinstance(result.coverage_data, CcCoverageData) if coverage else result.coverage_data is None


@pytest.mark.parametrize(
    "options, insufficient",
    [
        (["--gcovr-fail-under-line=40"], False),
        (["--gcovr-fail-under-line=60"], True),
        # Lines and functions both below: gcovr exits with 2 | 16.
        (["--gcovr-fail-under-line=60", "--gcovr-args=['--fail-under-function=60']"], True),
    ],
)
def test_fail_under(options: list[str], insufficient: bool) -> None:
    # Three of pick.c's six lines run, and one of its two functions.
    rule_runner = _rule_runner(*options)
    data = [_test(rule_runner, TESTS[0]).coverage_data]
    reports = rule_runner.request(CoverageReports, [CcCoverageDataCollection(data)])
    assert reports.coverage_insufficient is insufficient


def test_gcov_of_another_version_fails_before_the_build(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, version in (("gcc-13", "13.3.0"), ("gcov-13", "99.1.0"), ("gcov-tool-13", "99.1.0")):
        script = tools / name
        script.write_text(
            f'#!/bin/sh\n[ "$1" = -dumpversion ] && echo 13 || echo "{name} (stub) {version}"\n'
        )
        script.chmod(0o755)
    rule_runner = _rule_runner()
    toolchain = toolchain_build("stub", HOST, cc=str(tools / "gcc-13"))
    rule_runner.write_files({"BUILD": FILES["BUILD"] + platforms_build() + toolchain})
    with pytest.raises(
        ExecutionError,
        match=rf"compiler `{re.escape(str(tools))}/gcc-13`.*`{re.escape(str(tools))}/gcov-13` 99",
    ):
        _run(rule_runner, TESTS[0])
