from pants.engine.rules import collect_rules
from pants_backend_odin import dependency_inference
from pants_backend_odin import target_types as targets
from pants_backend_odin.goals import fmt, lint, package, tailor, test
from pants_backend_odin.util_rules import build


def target_types():
    return [
        *targets.targets(),
    ]


def rules():
    return [
        *collect_rules(),
        *targets.rules(),
        *dependency_inference.rules(),
        *build.rules(),
        *fmt.rules(),
        *lint.rules(),
        *package.rules(),
        *tailor.rules(),
        *test.rules(),
    ]
