"""clang-tidy for `cmake_project` targets: an optional sub-backend, since clang-tidy runs from a
pex and needs `pants.backend.python`, which `pants_backend_cmake` itself does not."""

from __future__ import annotations

from pants_backend_cmake.lint.clang_tidy import rules as clang_tidy_rules
from pants_backend_cmake.lint.clang_tidy import subsystem


def rules():
    return [*clang_tidy_rules.rules(), *subsystem.rules()]
