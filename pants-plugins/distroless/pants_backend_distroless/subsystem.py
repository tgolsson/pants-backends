from __future__ import annotations

from pants.option.option_types import DictOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class AptSubsystem(Subsystem):
    options_scope = "apt"
    help = "Options for resolving Debian packages into lockfiles."

    resolves = DictOption[str](
        default={},
        help=softwrap("""
        A mapping from a resolve name to the path of its lockfile, relative to the build root.

        Each resolve is declared by exactly one `deb_packages` target, and its lockfile is
        written by `pants generate-lockfiles --resolve=<name>`.
        """),
    )


def rules():
    return [*AptSubsystem.rules()]
