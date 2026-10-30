from __future__ import annotations

from pants.engine.target import StringSequenceField
from pants.util.strutil import softwrap


class CompileCostPathsField(StringSequenceField):
    alias = "compile_cost_paths"
    default = ()
    help = softwrap(
        """
        Directories, relative to this BUILD file, whose translation units `[compile-cost]`
        measures; none means the target is not measured. A translation unit compiled several
        times is measured once per compile.
        """
    )


class CompileCostExemptPathsField(StringSequenceField):
    alias = "compile_cost_exempt_paths"
    default = ()
    help = softwrap(
        """
        Directories, relative to this BUILD file, whose translation units `[compile-cost]` reports
        but does not hold to `[compile-cost].frontend_budget`. Each must be under
        `compile_cost_paths` and hold a measured translation unit.
        """
    )
