from __future__ import annotations

from pants.option.option_types import BoolOption, StrListOption, StrOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class PlatformsSubsystem(Subsystem):
    options_scope = "platforms"
    help = "Which platforms a run builds."

    enabled = StrListOption(
        default=[],
        help="Platforms `pants package` builds besides the host, for targets whose `platforms` list them.",
    )
    host = StrOption(
        default=None,
        help=softwrap(
            """
            The `platform` this machine is, when several match its os and arch. Unset, the only
            match, or an implicit `<os>-<arch>` platform when none matches.
            """
        ),
    )
    strict_toolchains = BoolOption(
        default=False,
        help=softwrap(
            """
            Require a `cc_toolchain` for every (host, target) pair built, native ones included.
            Unset, a native pair without one builds with `gcc`, `g++` and `ar` from the search
            path.
            """
        ),
    )
