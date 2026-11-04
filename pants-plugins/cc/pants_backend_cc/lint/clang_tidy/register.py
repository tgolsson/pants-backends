"""clang-tidy over the compile steps of targets with `clang_tidy_paths`: an optional sub-backend,
since clang-tidy runs from a pex and needs `pants.backend.python`."""

from __future__ import annotations

from pants_backend_cc import register as cc
from pants_backend_cc.lint.clang_tidy import rules as clang_tidy_rules
from pants_backend_cc.lint.clang_tidy import subsystem


def rules():
    return [*cc.rules(), *clang_tidy_rules.rules(), *subsystem.rules()]
