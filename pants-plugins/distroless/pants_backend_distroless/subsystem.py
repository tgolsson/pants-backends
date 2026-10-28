from __future__ import annotations

from pants.option.option_types import DictOption, StrOption
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

    resolver_pbs_release = StrOption(
        default="20261009",
        advanced=True,
        help=softwrap("""
        The python-build-standalone release `apt-resolver`, which resolves lockfiles, takes its
        Python from, such as `20261009`. It doesn't change what the lockfiles contain."""),
    )

    resolver_python_version = StrOption(
        default="3.11.17",
        advanced=True,
        help=softwrap("""
        The Python version, `<major>.<minor>.<patch>`, `apt-resolver` runs on. It must be in
        `[apt].resolver_pbs_release`. It doesn't change what the lockfiles contain."""),
    )


def rules():
    return [*AptSubsystem.rules()]
