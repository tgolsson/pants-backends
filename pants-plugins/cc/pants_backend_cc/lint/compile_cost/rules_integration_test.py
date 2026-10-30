"""compile-cost over the compile steps of `toy_cc`, a provider without a build system: the lint
depends on the compile-step interface alone."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from pants.core.goals.lint import LintResult
from pants.core.util_rules import source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.lint.compile_cost import register
from pants_backend_cc.lint.compile_cost.rules import CompileCostFieldSet, CompileCostRequest
from pants_backend_cc.lint.compile_cost.target_types import (
    CompileCostExemptPathsField,
    CompileCostPathsField,
)
from pants_backend_cc.testutil import ToyCcTarget, implicit_toolchain_is_gcc, toy_cc_rules
from pants_backend_platforms.testutil import HOST

FILES = {
    "BUILD": 'toy_cc(name="toy", sources=["src/*", "other/*"], compile_cost_paths=["src"])\n',
    "src/a.cpp": "int a() { return 1; }\n",
    "src/b.c": "int b(void) { return 2; }\n",
    "other/o.cpp": "int o() { return 3; }\n",
}

ENV = {"PATH", "PYENV_ROOT", "HOME"}
TOY = Address("", target_name="toy")

# A stand-in for gcc that counts its compiles of each source in the directory it is written to and
# prints a time report whose parsing time and system time differ between the first and the
# second; a source named `broken` fails to compile. Without `-ftime-report` it is the toolchain
# lookup asking for a version or a linker, and answers nothing in particular.
FAKE_GCC = """\
#!/bin/bash
case " $* " in *" -ftime-report "*) ;; *) echo "fake-gcc 1"; exit 0;; esac
while [ "$1" != -c ]; do shift; done
src=$2
case "$src" in *broken*) echo "$src: error: broken" >&2; exit 1;; esac
count="{dir}/$(basename "$src").count"
n=$(( $(cat "$count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$count"
if [ "$n" = 1 ]; then parsing=0.50; else parsing=0.20; fi
echo " phase parsing          : $parsing ( 80%)   0.0$n ( 90%)   0.50 ( 80%)    10M ( 80%)" >&2
echo " phase opt and generate :   0.10 ( 20%)   0.00 ( 10%)   0.10 ( 20%)     1M ( 20%)" >&2
echo " TOTAL                  :   0.60          0.0$n          0.60           11M" >&2
"""


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *source_files.rules(),
            *system_binaries.rules(),
            *register.rules(),
            *toy_cc_rules(),
            ToyCcTarget.register_plugin_field(CompileCostPathsField),
            ToyCcTarget.register_plugin_field(CompileCostExemptPathsField),
            QueryRule(LintResult, [CompileCostRequest.Batch]),
        ],
        target_types=[*cc_register.target_types(), ToyCcTarget],
    )


def _lint(rule_runner: RuleRunner, *options: str, **overrides: str) -> LintResult:
    rule_runner.write_files({**FILES, **overrides})
    rule_runner.set_options(list(options), env_inherit=ENV)
    field_set = CompileCostFieldSet.create(rule_runner.get_target(TOY))
    batch: Any = CompileCostRequest.Batch("", (field_set,), None)
    return rule_runner.request(LintResult, [batch])


@pytest.mark.skipif(not implicit_toolchain_is_gcc(), reason="the implicit toolchain must be gcc")
def test_measures_the_steps_under_the_paths(rule_runner: RuleRunner) -> None:
    result = _lint(rule_runner)
    assert result.exit_code == 0
    assert result.stdout == "compile-cost: 2 gated TUs, no frontend budget set\n"
    (report,) = rule_runner.request(DigestContents, [result.report])
    assert report.path == "compile-cost.md"
    text = report.content.decode()
    rows = sorted(line.split(" |")[0] for line in text.splitlines() if line.startswith("| "))
    assert rows == ["| TU", "| src/a.cpp", "| src/b.c"]


@pytest.mark.skipif(shutil.which("clang++") is None, reason="needs clang++ on the PATH")
def test_a_compiler_other_than_gcc_fails(rule_runner: RuleRunner) -> None:
    toolchain = (
        f'cc_toolchain(name="clang", host="{HOST}", target="{HOST}", cc="clang", cxx="clang++", ar="ar")\n'
    )
    with pytest.raises(
        ExecutionError,
        match=r"//:toy: `\S*clang\+\+` printed no gcc `-ftime-report` phases for src/a.cpp; "
        r"`\[compile-cost\]` measures gcc compiles only",
    ):
        _lint(rule_runner, BUILD=FILES["BUILD"].replace('"src/*"', '"src/a.cpp"') + toolchain)


def _fake_gcc(tmp_path: Path) -> str:
    """A `cc_toolchain` for the host whose compilers are `FAKE_GCC`, counting into `tmp_path`."""
    script = tmp_path / "fake-gcc"
    script.write_text(FAKE_GCC.format(dir=tmp_path))
    script.chmod(0o755)
    return (
        f'cc_toolchain(name="fake", host="{HOST}", target="{HOST}", cc="{script}", cxx="{script}", ar="ar")\n'
    )


def _rows(rule_runner: RuleRunner, result: LintResult) -> dict[str, list[str]]:
    (report,) = rule_runner.request(DigestContents, [result.report])
    rows = {}
    for line in report.content.decode().splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0].startswith("src/"):
            rows[cells[0]] = cells[1:]
    return rows


def test_a_tu_over_budget_is_measured_again_in_a_process_of_its_own(
    rule_runner: RuleRunner, tmp_path: Path
) -> None:
    result = _lint(
        rule_runner, "--compile-cost-frontend-budget=0.3", BUILD=FILES["BUILD"] + _fake_gcc(tmp_path)
    )
    # Had the engine reused the first sample for the second, each would have compiled once and
    # stayed at 0.5 s.
    assert {p.name: p.read_text() for p in tmp_path.glob("*.count")} == {
        "a.cpp.count": "2\n",
        "b.c.count": "2\n",
    }
    assert result.exit_code == 0
    assert _rows(rule_runner, result)["src/a.cpp"] == ["0.200", "0.020", "0.100", "0.600", "ok"]


def test_a_tu_within_budget_is_measured_once(rule_runner: RuleRunner, tmp_path: Path) -> None:
    result = _lint(
        rule_runner, "--compile-cost-frontend-budget=1", BUILD=FILES["BUILD"] + _fake_gcc(tmp_path)
    )
    assert result.exit_code == 0
    assert (tmp_path / "a.cpp.count").read_text() == "1\n"


def test_over_budget_the_summary_names_the_tus(rule_runner: RuleRunner, tmp_path: Path) -> None:
    result = _lint(
        rule_runner, "--compile-cost-frontend-budget=0.1", BUILD=FILES["BUILD"] + _fake_gcc(tmp_path)
    )
    assert result.exit_code == 1
    assert result.stdout == (
        "compile-cost: 2 of 2 gated TUs over the 0.1 s frontend budget\n"
        "  src/a.cpp: 0.200 s\n"
        "  src/b.c: 0.200 s\n"
    )


def test_a_failed_compile_fails(rule_runner: RuleRunner, tmp_path: Path) -> None:
    with pytest.raises(
        ExecutionError, match=r"//:toy: compiling src/broken.cpp failed:\nsrc/broken.cpp: error: broken"
    ):
        _lint(
            rule_runner,
            BUILD=FILES["BUILD"] + _fake_gcc(tmp_path),
            **{"src/broken.cpp": "int broken() { return 0; }\n"},
        )


@pytest.mark.parametrize(
    "exempt, error",
    [
        ("other", r"entry 'other' is not under `compile_cost_paths` \['src'\]"),
        ("src/kernel", r"entry 'src/kernel' matches no measured translation unit"),
    ],
)
def test_an_exempt_path_that_exempts_nothing_fails(
    rule_runner: RuleRunner, tmp_path: Path, exempt: str, error: str
) -> None:
    build = FILES["BUILD"].replace(")", f', compile_cost_exempt_paths=["{exempt}"])') + _fake_gcc(tmp_path)
    with pytest.raises(ExecutionError, match=rf"//:toy: `compile_cost_exempt_paths` {error}"):
        _lint(rule_runner, BUILD=build)
    assert not list(tmp_path.glob("*.count"))
