"""The rule runner the tests share: the CMake backend with pants-backend-cc and
pants-backend-platforms, plus what each test adds."""

from __future__ import annotations

from collections.abc import Iterable

from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.util_rules import env_vars, external_tool, source_files, system_binaries
from pants.engine.internals.parametrize import Parametrize
from pants.engine.target import Target
from pants.source import source_root
from pants.testutil.python_rule_runner import PythonRuleRunner
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cmake import register
from pants_backend_platforms import register as platforms_register


def cmake_rule_runner(
    *rules: object, target_types: Iterable[type[Target]] = (), python: bool = False
) -> RuleRunner:
    """`python` for a backend that runs a pex (clang-tidy, gcovr)."""
    runner = PythonRuleRunner if python else RuleRunner
    return runner(
        rules=[
            *(python_register.rules() if python else ()),
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *core_target_types.rules(),
            *env_vars.rules(),
            *source_root.rules(),
            *register.rules(),
            *rules,
        ],
        target_types=[
            *register.target_types(),
            *platforms_register.target_types(),
            *cc_register.target_types(),
            *target_types,
        ],
        objects={"parametrize": Parametrize},
    )
