from pants_plugin_deb import subsystem, target_generators, targets
from pants_plugin_deb.goals import lockfile, package


def target_types():
    return [
        *targets.targets(),
        *target_generators.targets(),
    ]


def rules():
    return [
        *lockfile.rules(),
        *package.rules(),
        *target_generators.rules(),
        *subsystem.rules(),
    ]
