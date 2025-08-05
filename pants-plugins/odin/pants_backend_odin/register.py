from pants.engine.rules import collect_rules
from pants_backend_odin import dependency_inference
from pants_backend_odin import target_types as targets
from pants_backend_odin.goals import lint, tailor


def target_types():
    return [
        *targets.targets(),
    ]


def rules():
    return [
        *collect_rules(),
        *targets.rules(),
        *dependency_inference.rules(),
        *lint.rules(),
        *tailor.rules(),
    ]
