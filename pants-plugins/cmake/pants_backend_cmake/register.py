from pants_backend_cmake import target_types as targets_module
from pants_backend_cmake.goals import package, test
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


def target_types():
    return targets_module.targets()


def rules():
    return [
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
    ]
