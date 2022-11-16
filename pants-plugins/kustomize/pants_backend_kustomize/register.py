from pants.engine.rules import collect_rules

from pants_backend_kustomize import codegen
from pants_backend_kustomize import target_types as targets
from pants_backend_kustomize.util_rules import prepare_context


def target_types():
    return [
        *targets.targets(),
    ]


def rules():
    return [*collect_rules(), *targets.rules(), *codegen.rules(), *prepare_context.rules()]
