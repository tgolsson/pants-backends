from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable

from pants.backend.python.subsystems.python_tool_base import PythonToolBase
from pants.backend.python.target_types import ConsoleScript
from pants.core.goals.resolves import ExportableTool
from pants.core.util_rules.config_files import ConfigFilesRequest
from pants.engine.rules import Rule, collect_rules
from pants.engine.unions import UnionRule
from pants.option.option_types import ArgsListOption, SkipOption, StrListOption
from pants.util.strutil import softwrap


class ClangTidy(PythonToolBase):
    options_scope = "clang-tidy"
    name = "clang-tidy"
    help_short = softwrap(
        """
        clang-tidy (https://clang.llvm.org/extra/clang-tidy/) over the translation units of
        `cmake_project` targets, from its PyPI wheel (https://pypi.org/project/clang-tidy/).
        """
    )

    default_main = ConsoleScript("clang-tidy")
    default_requirements = ["clang-tidy==18.1.8"]

    register_interpreter_constraints = True
    default_interpreter_constraints = ["CPython>=3.9,<3.15"]

    default_lockfile_resource = ("pants_backend_cmake.lint.clang_tidy", "clang_tidy.lock")

    skip = SkipOption("lint")
    args = ArgsListOption(example="--checks=-*,bugprone-*")
    drop_compiler_args = StrListOption(
        default=[],
        help=softwrap(
            """
            `fnmatch` patterns of compile arguments clang rejects, such as gcc-only `-f` and
            `-m` options, removed before clang-tidy sees the command.
            """
        ),
    )

    def keeps(self, compiler_arg: str) -> bool:
        return not any(fnmatch.fnmatchcase(compiler_arg, p) for p in self.drop_compiler_args)

    def config_request(self, dirs: Iterable[str]) -> ConfigFilesRequest:
        """clang-tidy reads the `.clang-tidy` files from a translation unit's directory up to the
        root, so add all of them."""
        return ConfigFilesRequest(
            discovery=True,
            check_existence=[os.path.join(d, ".clang-tidy") for d in ("", *dirs)],
        )


def rules() -> Iterable[Rule | UnionRule]:
    return [*collect_rules(), UnionRule(ExportableTool, ClangTidy)]
