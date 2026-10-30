from __future__ import annotations

from pants_backend_platforms.target_types import PlatformTarget
from pants_backend_platforms.util_rules import contract, lookup, platforms


def target_types():
    return [PlatformTarget]


def rules():
    return [*lookup.rules(), *platforms.rules(), *contract.rules()]
