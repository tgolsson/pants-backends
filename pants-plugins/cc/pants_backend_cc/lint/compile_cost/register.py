"""gcc `-ftime-report` over the compile steps of targets with `compile_cost_paths`: an optional
sub-backend of `pants lint`."""

from __future__ import annotations

from pants_backend_cc import register as cc
from pants_backend_cc.lint.compile_cost import rules as compile_cost_rules


def rules():
    return [*cc.rules(), *compile_cost_rules.rules()]
