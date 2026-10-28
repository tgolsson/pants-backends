from __future__ import annotations

from pants_backend_platforms.target_types import CcToolchainTarget, PlatformTarget
from pants_backend_platforms.util_rules import contract, platforms, toolchain


def target_types():
    return [PlatformTarget, CcToolchainTarget]


def rules():
    return [*platforms.rules(), *toolchain.rules(), *contract.rules()]
