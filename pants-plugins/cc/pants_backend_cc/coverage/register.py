"""C/C++ test coverage with gcovr: an optional sub-backend, since gcovr runs from a pex and needs
`pants.backend.python`."""

from __future__ import annotations

from pants_backend_cc import register as cc
from pants_backend_cc.coverage import rules as coverage_rules
from pants_backend_cc.coverage import subsystem


def rules():
    return [*cc.rules(), *coverage_rules.rules(), *subsystem.rules()]
