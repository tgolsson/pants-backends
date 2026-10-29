"""clang-tidy from the default lockfile on a small project built with the real CMake and the host
compiler."""

from __future__ import annotations

from typing import Any

import pytest
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals.lint import LintResult
from pants.core.util_rules import (
    config_files,
    env_vars,
    external_tool,
    source_files,
    system_binaries,
)
from pants.engine.addresses import Address
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.source import source_root
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_cmake import register as cmake_register
from pants_backend_cmake.lint.clang_tidy import register
from pants_backend_cmake.lint.clang_tidy.rules import ClangTidyFieldSet, ClangTidyRequest
from pants_backend_cmake.testutil import GenTarget, codegen_rules
from pants_backend_platforms import register as platforms_register

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C CXX)
add_library(demo STATIC src/impl/main.cpp src/recursive.cpp src/shared.cpp src/quiet/quiet.cpp
            src/c/rec.c)
target_include_directories(demo PRIVATE ${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/vendor)
# gcc-only: an unknown warning clang-tidy ignores, an unknown argument it must not see.
target_compile_options(demo PRIVATE -Wlogical-op -fipa-pta)
add_library(twin STATIC src/shared.cpp)
add_library(other STATIC other/recursive.cpp)
"""

RECURSIVE = "int {name}(int n) {{ return n <= 1 ? 1 : n * {name}(n - 1); }}\n"
ITERATIVE = "int {name}(int n) {{ int r = 1; while (n > 1) r *= n--; return r; }}\n"
CONFIG = "Checks: '-*,misc-no-recursion,misc-definitions-in-headers'\nWarningsAsErrors: '*'\n"

# Functions defined in headers: `local.h` is found next to `main.cpp`, `api.h` and `vendored.h`
# through `-I` (clang-tidy sees differently spelled paths), `gen.h` is generated. `shared.cpp`
# is compiled by two targets; `quiet/` has its own `.clang-tidy` without the recursion check.
FILES = {
    "BUILD": 'test_gen(name="gen", sources=["src/gen.h.in"])\n'
    'cmake_project(name="proj", sources=["CMakeLists.txt", "src/**", "vendor/*", "other/*"], '
    'dependencies=[":gen"], clang_tidy_paths=["src"])\n',
    "CMakeLists.txt": CMAKELISTS,
    ".clang-tidy": CONFIG,
    "src/impl/main.cpp": '#include "local.h"\n#include "api.h"\n#include "vendored.h"\n'
    '#include "gen.h"\nint run(int n) { return local(n) + api(n) + vendored(n) + gen(n); }\n',
    "src/impl/local.h": "int local(int n) { return n; }\n",
    "src/api.h": "int api(int n) { return n; }\n",
    "src/gen.h.in": "int gen(int n) { return n; }\n",
    "src/recursive.cpp": RECURSIVE.format(name="fact"),
    "src/shared.cpp": RECURSIVE.format(name="twice"),
    "src/quiet/.clang-tidy": "Checks: '-*,misc-definitions-in-headers'\nWarningsAsErrors: '*'\n",
    "src/quiet/quiet.cpp": RECURSIVE.format(name="quiet"),
    "src/c/rec.c": RECURSIVE.format(name="crec"),
    "vendor/vendored.h": "int vendored(int n) { return n; }\n",
    "other/recursive.cpp": RECURSIVE.format(name="other"),
}

CLEAN = {
    "src/impl/local.h": "inline int local(int n) { return n; }\n",
    "src/api.h": "inline int api(int n) { return n; }\n",
    "src/gen.h.in": "inline int gen(int n) { return n; }\n",
    "src/recursive.cpp": ITERATIVE.format(name="fact"),
    "src/shared.cpp": ITERATIVE.format(name="twice"),
    "src/c/rec.c": ITERATIVE.format(name="crec"),
}

ENV = {"PATH", "PYENV_ROOT", "HOME"}
DROP = "--clang-tidy-drop-compiler-args=['-fipa-pta']"
PROJ = Address("", target_name="proj")


@pytest.fixture
def rule_runner() -> PythonRuleRunner:
    return PythonRuleRunner(
        rules=[
            *python_register.rules(),
            *core_target_types.rules(),
            *config_files.rules(),
            *source_files.rules(),
            *external_tool.rules(),
            *system_binaries.rules(),
            *env_vars.rules(),
            *source_root.rules(),
            *cmake_register.rules(),
            *register.rules(),
            *codegen_rules(),
            QueryRule(LintResult, [ClangTidyRequest.Batch]),
        ],
        target_types=[*cmake_register.target_types(), *platforms_register.target_types(), GenTarget],
    )


def _lint(
    rule_runner: PythonRuleRunner,
    *options: str,
    address: Address = PROJ,
    files: dict[str, str] = FILES,
    **overrides: str,
) -> LintResult:
    rule_runner.write_files({**files, **overrides})
    rule_runner.set_options(list(options), env_inherit=ENV)
    field_set = ClangTidyFieldSet.create(rule_runner.get_target(address))
    batch: Any = ClangTidyRequest.Batch("", (field_set,), None)
    return rule_runner.request(LintResult, [batch])


def _reported(result: LintResult, diagnostic: str) -> int:
    return sum(line.startswith(diagnostic) for line in result.stdout.splitlines())


def test_violations_fail(rule_runner: PythonRuleRunner) -> None:
    result = _lint(rule_runner, DROP)
    assert result.exit_code == 1
    # Files are named relative to the build root, however the compiler found them, and each
    # finding once, though `shared.cpp` is compiled twice.
    expected = [
        "src/recursive.cpp:1:5: error: function 'fact' is within a recursive call chain",
        "src/shared.cpp:1:5: error: function 'twice' is within a recursive call chain",
        "src/c/rec.c:1:5: error: function 'crec' is within a recursive call chain",
        "src/impl/local.h:1:5: error: function 'local' defined in a header file",
        "src/api.h:1:5: error: function 'api' defined in a header file",
        "src/gen.h:1:5: error: function 'gen' defined in a header file",
    ]
    assert [_reported(result, d) for d in expected] == [1] * len(expected), result.stdout
    # Not the header from `vendor`, the translation unit under `other`, nor the check the nested
    # `.clang-tidy` turns off.
    for name in ("'vendored'", "'other'", "'quiet'"):
        assert name not in result.stdout
    assert result.stderr == "clang-tidy: 5 of 6 compiles failed\n"


def test_clean_passes(rule_runner: PythonRuleRunner) -> None:
    result = _lint(rule_runner, DROP, **CLEAN)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stderr == "clang-tidy: 0 of 6 compiles failed\n"


def test_config_edit_changes_the_result(rule_runner: PythonRuleRunner) -> None:
    assert _lint(rule_runner, DROP).exit_code == 1
    relaxed = CONFIG.replace("WarningsAsErrors: '*'", "WarningsAsErrors: ''")
    result = _lint(rule_runner, DROP, **{".clang-tidy": relaxed})
    assert result.exit_code == 0, result.stdout + result.stderr


def test_unknown_compiler_argument_fails(rule_runner: PythonRuleRunner) -> None:
    result = _lint(rule_runner)
    assert result.exit_code == 1
    assert _reported(result, "error: unknown argument: '-fipa-pta'") == 1


def test_project_below_the_build_root(rule_runner: PythonRuleRunner) -> None:
    files = {
        ".clang-tidy": CONFIG,
        "sub/BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "src/*"], '
        'clang_tidy_paths=["src"])\n',
        "sub/CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(sub LANGUAGES CXX)\n"
        "add_library(sub STATIC src/r.cpp)\n",
        "sub/src/r.cpp": RECURSIVE.format(name="nested"),
    }
    result = _lint(rule_runner, address=Address("sub", target_name="proj"), files=files)
    assert result.exit_code == 1
    assert _reported(result, "sub/src/r.cpp:1:5: error: function 'nested'") == 1


def test_without_paths_the_project_is_not_linted(rule_runner: PythonRuleRunner) -> None:
    build = FILES["BUILD"].replace(', clang_tidy_paths=["src"]', "")
    rule_runner.write_files({**FILES, "BUILD": build})
    assert not ClangTidyFieldSet.is_applicable(rule_runner.get_target(PROJ))


def test_paths_without_translation_units_fail(rule_runner: PythonRuleRunner) -> None:
    build = FILES["BUILD"].replace('clang_tidy_paths=["src"]', 'clang_tidy_paths=["scr"]')
    with pytest.raises(
        ExecutionError, match=r"//:proj: no translation unit is under `clang_tidy_paths` \['scr'\]"
    ):
        _lint(rule_runner, DROP, BUILD=build)
