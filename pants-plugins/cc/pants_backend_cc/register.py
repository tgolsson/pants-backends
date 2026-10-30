from __future__ import annotations

from pants_backend_cc.goals import package, test, wrapper
from pants_backend_cc.target_types import (
    CcBinaryTarget,
    CcTestsGeneratorTarget,
    CcTestTarget,
    CcToolchainTarget,
)
from pants_backend_cc.util_rules import compile, executable, generator, library, outputs, toolchain
from pants_backend_platforms import register as platforms


def target_types():
    return [CcToolchainTarget, CcTestTarget, CcTestsGeneratorTarget, CcBinaryTarget]


def rules():
    return [
        *platforms.rules(),
        *toolchain.rules(),
        *compile.rules(),
        *library.rules(),
        *outputs.rules(),
        *executable.rules(),
        *generator.rules(),
        *test.rules(),
        *package.rules(),
        *wrapper.rules(),
    ]
