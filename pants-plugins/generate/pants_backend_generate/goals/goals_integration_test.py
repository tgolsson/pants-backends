from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pants.core.goals.lint import Lint, LintResult
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_generate.goals.generate import Generate
from pants_backend_generate.goals.lint import GenerateLintRequest
from pants_backend_generate.target_types import GenerateFieldSet
from pants_backend_generate.testutil import generate_build, rule_runner, write_project

GEN = Address("proj", target_name="gen")
ENV = {"PATH", "PYENV_ROOT", "HOME"}
CURRENT = {"proj/out/a.txt": "a.txt: hello\n", "proj/out/b.txt": "b.txt: hello\n"}


@pytest.fixture
def runner() -> PythonRuleRunner:
    return rule_runner(QueryRule(LintResult, [GenerateLintRequest.Batch]))


def _read(runner: PythonRuleRunner, path: str) -> str | None:
    file = Path(runner.build_root, path)
    return file.read_text() if file.exists() else None


def _lint(runner: PythonRuleRunner, address: Address) -> LintResult:
    field_set = GenerateFieldSet.create(runner.get_target(address))
    batch = GenerateLintRequest.Batch("generate", (field_set,), None)
    return runner.request(LintResult, [batch])


def test_generate_writes_the_build_root(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build()})
    result = runner.run_goal_rule(Generate, args=["proj:gen"], env_inherit=ENV)
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "proj:gen: wrote proj/out/a.txt, proj/out/b.txt\n"
    assert {path: _read(runner, path) for path in CURRENT} == CURRENT
    assert _read(runner, "proj/out/notes.md") == "not generated\n"


def test_generate_refuses_two_writers_of_one_file(runner: PythonRuleRunner) -> None:
    build = generate_build() + generate_build().replace('name="gen"', 'name="again"')
    write_project(runner, {"proj/BUILD": build})
    result = runner.run_goal_rule(Generate, args=["proj::"], env_inherit=ENV)
    assert result.exit_code == 1
    assert "proj/out/a.txt: proj:again, proj:gen" in result.stderr
    assert _read(runner, "proj/out/a.txt") is None


def test_lint_current(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build(), **CURRENT})
    result = _lint(runner, GEN)
    assert (result.exit_code, result.stderr) == (0, "")
    lint = runner.run_goal_rule(Lint, args=["proj:gen"], env_inherit=ENV)
    assert lint.exit_code == 0
    assert "✓ generate succeeded." in lint.stderr


def test_no_generate_targets(runner: PythonRuleRunner) -> None:
    write_project(runner, {})
    assert runner.run_goal_rule(Generate, args=["tools::"], env_inherit=ENV).exit_code == 0
    assert runner.run_goal_rule(Lint, args=["tools::"], env_inherit=ENV).exit_code == 0


def test_lint_stale_and_missing(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build(), "proj/out/a.txt": "a.txt: an older hello\n"})
    result = _lint(runner, GEN)
    assert result.exit_code == 1
    assert result.linter_name == "generate"
    assert result.stderr == dedent(
        """\
        proj:gen: proj/out/a.txt: stale
        proj:gen: proj/out/b.txt: missing

        Run `pants generate proj:gen` to refresh."""
    )
    assert runner.run_goal_rule(Lint, args=["proj:gen"], env_inherit=ENV).exit_code == 1
    skipped = runner.run_goal_rule(Lint, args=["--generate-skip", "proj:gen"], env_inherit=ENV)
    assert skipped.exit_code == 0
