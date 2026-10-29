from pants_backend_cc import register as cc
from pants_backend_cmake import target_types as targets_module
from pants_backend_cmake.goals import package
from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.util_rules import (
    compile,
    configure,
    link,
    replay,
    scan,
    targets,
    toolchain,
)


def target_types():
    return targets_module.targets()


def rules():
    return [
        *cc.rules(),
        *cmake.rules(),
        *ninja.rules(),
        *toolchain.rules(),
        *configure.rules(),
        *targets.rules(),
        *scan.rules(),
        *compile.rules(),
        *replay.rules(),
        *link.rules(),
        *package.rules(),
    ]
