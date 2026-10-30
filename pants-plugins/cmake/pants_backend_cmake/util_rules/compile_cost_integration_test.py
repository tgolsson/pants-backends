"""compile-cost over the compile steps of a small project configured with the real CMake and the
implicit host toolchain, which must be gcc."""

from __future__ import annotations

import re
from typing import Any

import pytest
from pants.core.goals.lint import LintResult
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.rules import QueryRule
from pants.source import source_root
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.lint.compile_cost import register
from pants_backend_cc.lint.compile_cost.rules import CompileCostFieldSet, CompileCostRequest
from pants_backend_cc.testutil import implicit_toolchain_is_gcc
from pants_backend_cmake import register as cmake_register
from pants_backend_platforms import register as platforms_register

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C CXX)
add_library(demo STATIC src/heavy.cpp src/light.c src/kernels/k.cpp)
add_library(other STATIC other/o.cpp)
add_library(demo_shared SHARED src/light.c)
"""

# `heavy.cpp` parses a large header, so its frontend is never below gcc's 10 ms resolution.
HEAVY = '#include <regex>\nbool heavy(const char* s) { return std::regex_match(s, std::regex("a+")); }\n'

FILES = {
    "BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "src/**", "other/*"], '
    'compile_cost_paths=["src"], compile_cost_exempt_paths=["src/kernels"])\n',
    "CMakeLists.txt": CMAKELISTS,
    "src/heavy.cpp": HEAVY,
    "src/light.c": "int light(void) { return 1; }\n",
    "src/kernels/k.cpp": HEAVY.replace("heavy", "kernel"),
    "other/o.cpp": "int o() { return 3; }\n",
}

pytestmark = pytest.mark.skipif(not implicit_toolchain_is_gcc(), reason="the implicit toolchain must be gcc")

ENV = {"PATH", "PYENV_ROOT", "HOME"}
# `src/light.c` is in two libraries, so each of its rows names its object.
LIGHT = "src/light.c (build/CMakeFiles/demo.dir/src/light.c.o)"
LIGHT_SHARED = "src/light.c (build/CMakeFiles/demo_shared.dir/src/light.c.o)"
# Goals that expand generators see `#_inputs`, not the project; it carries the paths.
INPUTS = Address("", target_name="proj", generated_name="_inputs")


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *env_vars.rules(),
            *source_root.rules(),
            *cmake_register.rules(),
            *register.rules(),
            QueryRule(LintResult, [CompileCostRequest.Batch]),
        ],
        target_types=[
            *cmake_register.target_types(),
            *platforms_register.target_types(),
            *cc_register.target_types(),
        ],
    )


def _lint(rule_runner: RuleRunner, *options: str) -> tuple[LintResult, dict[str, str]]:
    rule_runner.write_files(FILES)
    rule_runner.set_options(list(options), env_inherit=ENV)
    field_set = CompileCostFieldSet.create(rule_runner.get_target(INPUTS))
    batch: Any = CompileCostRequest.Batch("", (field_set,), None)
    result = rule_runner.request(LintResult, [batch])
    (report,) = rule_runner.request(DigestContents, [result.report])
    assert report.path == "compile-cost.md"
    rows = {}
    for line in report.content.decode().splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0].startswith("src/"):
            rows[cells[0]] = cells[-1]
    return result, rows


def test_without_a_budget_it_reports(rule_runner: RuleRunner) -> None:
    result, rows = _lint(rule_runner)
    assert result.exit_code == 0
    assert result.stdout == "compile-cost: 3 gated TUs, no frontend budget set\n"
    assert rows == {
        "src/heavy.cpp": "no budget",
        LIGHT: "no budget",
        LIGHT_SHARED: "no budget",
        "src/kernels/k.cpp": "not gated",
    }


def test_within_the_budget_passes(rule_runner: RuleRunner) -> None:
    result, rows = _lint(rule_runner, "--compile-cost-frontend-budget=100")
    assert result.exit_code == 0
    assert result.stdout == "compile-cost: 0 of 3 gated TUs over the 100 s frontend budget\n"
    assert rows["src/heavy.cpp"] == "ok"


def test_over_the_budget_fails(rule_runner: RuleRunner) -> None:
    result, rows = _lint(rule_runner, "--compile-cost-frontend-budget=0.001")
    assert result.exit_code == 1
    assert rows["src/heavy.cpp"] == "**over**"
    # The verdict, then the TUs over budget with their frontend time; gcc rounds a C file this
    # small to 0 s, which is never over.
    lines = result.stdout.splitlines()
    assert re.fullmatch(r"compile-cost: [1-3] of 3 gated TUs over the 0.001 s frontend budget", lines[0])
    assert any(re.fullmatch(r"  src/heavy\.cpp: \d+\.\d{3} s", line) for line in lines[1:])
    assert len(lines) == 1 + sum(status == "**over**" for status in rows.values())
    # An exempt TU is never over, however slow.
    assert rows["src/kernels/k.cpp"] == "not gated"


def test_without_paths_the_project_is_not_measured(rule_runner: RuleRunner) -> None:
    build = FILES["BUILD"].replace(', compile_cost_paths=["src"]', "")
    rule_runner.write_files({**FILES, "BUILD": build})
    assert not CompileCostFieldSet.is_applicable(rule_runner.get_target(INPUTS))
