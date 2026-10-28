from pants_backend_cmake import target_types as targets_module
from pants_backend_cmake.goals import package, test, wrapper
from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.util_rules import (
    configure,
    executable,
    generate,
    replay,
    scan,
    targets,
    toolchain,
)
from pants_backend_platforms import register as platforms


def target_types():
    return targets_module.targets()


def rules():
    return [
        *platforms.rules(),
        *cmake.rules(),
        *ninja.rules(),
        *toolchain.rules(),
        *configure.rules(),
        *targets.rules(),
        *scan.rules(),
        *replay.rules(),
        *executable.rules(),
        *generate.rules(),
        *package.rules(),
        *test.rules(),
        *wrapper.rules(),
    ]
