from __future__ import annotations

from pants.option.option_types import BoolOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class CcToolchainsSubsystem(Subsystem):
    options_scope = "cc-toolchains"
    help = "How the C and C++ tools are chosen for a platform."

    strict = BoolOption(
        default=False,
        help=softwrap(
            """
            Require a `cc_toolchain` for every (host, target) pair built, native ones included.
            Unset, a native pair without one builds with `gcc`, `g++` and `ar` from the search
            path.
            """
        ),
    )
