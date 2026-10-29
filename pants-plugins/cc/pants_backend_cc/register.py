from __future__ import annotations

from pants_backend_cc.goals import test, wrapper
from pants_backend_cc.target_types import CcTestsGeneratorTarget, CcTestTarget, CcToolchainTarget
from pants_backend_cc.util_rules import compile, executable, generator, library, toolchain
from pants_backend_platforms import register as platforms


def target_types():
    return [CcToolchainTarget, CcTestTarget, CcTestsGeneratorTarget]


def rules():
    return [
        *platforms.rules(),
        *toolchain.rules(),
        *compile.rules(),
        *library.rules(),
        *executable.rules(),
        *generator.rules(),
        *test.rules(),
        *wrapper.rules(),
    ]
