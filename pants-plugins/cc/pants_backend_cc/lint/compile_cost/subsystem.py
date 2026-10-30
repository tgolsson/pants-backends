from __future__ import annotations

from pants.option.option_types import FloatOption, SkipOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class CompileCost(Subsystem):
    options_scope = "compile-cost"
    name = "compile-cost"
    help = softwrap(
        """
        The compile cost of the translation units of targets with `compile_cost_paths`: each
        host compile step runs once more with gcc's `-ftime-report`, and its user CPU time per
        phase, with the frontend's system time beside it, goes into `compile-cost.md`, a report
        under the `lint` goal's directory in `dist/`.
        """
    )

    skip = SkipOption("lint")
    frontend_budget = FloatOption(
        default=None,
        help=softwrap(
            """
            Seconds of frontend user CPU time (gcc's parsing and deferred language phases) a
            translation unit outside `compile_cost_exempt_paths` may take; one over it fails the
            lint. System time is not gated, as it varies with the load on the machine. Unset, the
            lint only reports.
            """
        ),
    )
