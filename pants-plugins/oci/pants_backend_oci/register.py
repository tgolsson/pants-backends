from pants.engine.rules import collect_rules

from pants_backend_oci import goals, subsystem, targets, util_rules
from pants_backend_oci.tools import process


def target_types():
    return [
        *targets.targets(),
    ]


def rules():
    return [
        *collect_rules(),
        *goals.rules(),
        *util_rules.rules(),
        *subsystem.rules(),
        *targets.rules(),
        *process.rules(),
    ]
