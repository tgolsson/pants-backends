"""clang-tidy from the default lockfile over the compile steps of `toy_cc`, a provider without a
build system: the lint depends on the compile-step interface alone."""

from __future__ import annotations

from typing import Any

import pytest
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals.lint import LintResult
from pants.core.util_rules import config_files, external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.lint.clang_tidy import register
from pants_backend_cc.lint.clang_tidy.rules import ClangTidyFieldSet, ClangTidyRequest
from pants_backend_cc.lint.clang_tidy.target_types import ClangTidyPathsField
from pants_backend_cc.testutil import SHADOW_RULES, ToyCcTarget, toy_cc_rules

RECURSIVE = "int {name}(int n) {{ return n <= 1 ? 1 : n * {name}(n - 1); }}\n"
ITERATIVE = "int {name}(int n) {{ int r = 1; while (n > 1) r *= n--; return r; }}\n"

# Headers defining functions: `h.h` is found through `-I.` by two translation units, `local.h`
# next to the file including it, `other/o.h` is outside `clang_tidy_paths` like `other/o.cpp`.
FILES = {
    "BUILD": 'toy_cc(name="toy", sources=["src/*", "other/*"], clang_tidy_paths=["src"])\n',
    ".clang-tidy": "Checks: '-*,misc-no-recursion,misc-definitions-in-headers'\nWarningsAsErrors: '*'\n",
    "src/h.h": "int h(int n) { return n; }\n",
    "src/local.h": "int local(int n) { return n; }\n",
    "src/a.cpp": '#include "src/h.h"\n#include "other/o.h"\n' + RECURSIVE.format(name="fact"),
    "src/b.cpp": '#include "src/h.h"\n#include "local.h"\n' + ITERATIVE.format(name="iter"),
    "other/o.h": "int o(int n) { return n; }\n",
    "other/o.cpp": RECURSIVE.format(name="other"),
}

ENV = {"PATH", "PYENV_ROOT", "HOME"}
TOY = Address("", target_name="toy")


def _rule_runner(*extra) -> PythonRuleRunner:
    return PythonRuleRunner(
        rules=[
            *python_register.rules(),
            *core_target_types.rules(),
            *config_files.rules(),
            *source_files.rules(),
            *external_tool.rules(),
            *system_binaries.rules(),
            *register.rules(),
            *toy_cc_rules(),
            ToyCcTarget.register_plugin_field(ClangTidyPathsField),
            QueryRule(LintResult, [ClangTidyRequest.Batch]),
            *extra,
        ],
        target_types=[*cc_register.target_types(), ToyCcTarget],
    )


@pytest.fixture
def rule_runner() -> PythonRuleRunner:
    return _rule_runner()


def _lint(rule_runner: PythonRuleRunner, **overrides: str) -> LintResult:
    rule_runner.write_files({**FILES, **overrides})
    rule_runner.set_options([], env_inherit=ENV)
    field_set = ClangTidyFieldSet.create(rule_runner.get_target(TOY))
    batch: Any = ClangTidyRequest.Batch("", (field_set,), None)
    return rule_runner.request(LintResult, [batch])


def _reported(result: LintResult, diagnostic: str) -> int:
    return sum(line.startswith(diagnostic) for line in result.stdout.splitlines())


def test_violations_fail(rule_runner: PythonRuleRunner) -> None:
    result = _lint(rule_runner)
    assert result.exit_code == 1
    expected = [
        "src/a.cpp:3:5: error: function 'fact' is within a recursive call chain",
        "src/h.h:1:5: error: function 'h' defined in a header file",
        "src/local.h:1:5: error: function 'local' defined in a header file",
    ]
    assert [_reported(result, d) for d in expected] == [1] * len(expected), result.stdout
    for name in ("'other'", "'o'"):
        assert name not in result.stdout
    assert result.stderr == "clang-tidy: 2 of 2 compiles failed\n"


def test_warnings_pass_and_are_reported(rule_runner: PythonRuleRunner) -> None:
    result = _lint(
        rule_runner, **{".clang-tidy": FILES[".clang-tidy"].replace("WarningsAsErrors: '*'\n", "")}
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _reported(result, "src/a.cpp:3:5: warning: function 'fact'") == 1, result.stdout
    assert result.stderr == "clang-tidy: 0 of 2 compiles failed\n"


def test_clean_passes(rule_runner: PythonRuleRunner) -> None:
    clean = {
        "src/h.h": "inline int h(int n) { return n; }\n",
        "src/local.h": "inline int local(int n) { return n; }\n",
        "src/a.cpp": '#include "src/h.h"\n' + ITERATIVE.format(name="fact"),
    }
    result = _lint(rule_runner, **clean)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stderr == "clang-tidy: 0 of 2 compiles failed\n"


def test_paths_without_translation_units_fail(rule_runner: PythonRuleRunner) -> None:
    build = FILES["BUILD"].replace('clang_tidy_paths=["src"]', 'clang_tidy_paths=["scr"]')
    with pytest.raises(
        ExecutionError, match=r"//:toy: no translation unit is under `clang_tidy_paths` \['scr'\]"
    ):
        _lint(rule_runner, BUILD=build)


def test_a_target_with_two_providers_fails() -> None:
    with pytest.raises(
        ExecutionError,
        match=r"//:toy: `clang_tidy_paths` needs exactly one compile-step provider for the target, "
        r"found \['ToyCompileStepsFieldSet', 'ToyShadowFieldSet'\]",
    ):
        _lint(_rule_runner(*SHADOW_RULES))
