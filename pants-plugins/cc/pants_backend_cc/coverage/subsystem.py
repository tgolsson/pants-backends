from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from pants.backend.python.subsystems.python_tool_base import PythonToolBase
from pants.backend.python.target_types import ConsoleScript
from pants.core.goals.resolves import ExportableTool
from pants.engine.rules import Rule, collect_rules
from pants.engine.unions import UnionRule
from pants.option.option_types import ArgsListOption, EnumListOption, FloatOption, StrListOption
from pants.util.strutil import softwrap


class GcovrReportType(Enum):
    CONSOLE = "console"
    HTML = "html"
    JSON = "json"
    JSON_SUMMARY = "json-summary"
    COBERTURA = "cobertura"
    LCOV = "lcov"
    MARKDOWN = "markdown"


class Gcovr(PythonToolBase):
    options_scope = "gcovr"
    name = "gcovr"
    help_short = softwrap(
        """
        gcovr (https://gcovr.com) over the gcov data of `cc_test`s run with
        `pants test --use-coverage`, merged into one report.
        """
    )

    default_main = ConsoleScript("gcovr")
    default_requirements = ["gcovr==8.8"]

    register_interpreter_constraints = True
    default_interpreter_constraints = ["CPython>=3.10,<3.15"]

    default_lockfile_resource = ("pants_backend_cc.coverage", "gcovr.lock")

    report = EnumListOption(
        default=[GcovrReportType.CONSOLE],
        help=softwrap(
            """
            The reports to write: `console` prints gcovr's summary; the others are files under
            `dist/coverage/cc/` (`html/index.html`, `coverage.json`, `coverage-summary.json`,
            `coverage.xml`, `coverage.lcov`, `coverage.md`).
            """
        ),
    )
    filter = StrListOption(
        default=[],
        help=softwrap(
            """
            gcovr `--filter` regular expressions: only source files matching one, relative to the
            build root, are reported. Unset, every file under the build root is.
            """
        ),
    )
    exclude = StrListOption(
        default=[],
        help="gcovr `--exclude` regular expressions: source files matching one are not reported.",
    )
    fail_under_line = FloatOption(
        default=None,
        help="Fail the test goal when the merged line coverage, in percent, is below this.",
    )
    args = ArgsListOption(
        example="--exclude-unreachable-branches",
        extra_help=softwrap(
            """
            They are given to every gcovr run: those that read the tests' gcov data, and the one
            that writes the reports. A `--fail-under-*` threshold among them fails the goal as
            `fail_under_line` does.
            """
        ),
    )


def rules() -> Iterable[Rule | UnionRule]:
    return [*collect_rules(), UnionRule(ExportableTool, Gcovr)]
