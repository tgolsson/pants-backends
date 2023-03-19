from pants.engine.rules import collect_rules

from pants_backend_oci import (
    goals,
    language_target,
    subsystem,
    synthetic_targets,
    targets,
    util_rules,
)
from pants_backend_oci.rules import kustomize_inject
from pants_backend_oci.tools import process


def target_types():
    return [
        *language_target.targets(),
        *targets.targets(),
    ]


def rules():
    return [
        *collect_rules(),
        *goals.rules(),
        *kustomize_inject.rules(),
        *language_target.rules(),
        *process.rules(),
        *subsystem.rules(),
        *targets.rules(),
        *util_rules.rules(),
        *synthetic_targets.rules(),
    ]
