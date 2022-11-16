from pants_backend_k8s import target_generators
from pants_backend_k8s import target_types as targets
from pants_backend_k8s.goals import run


def target_types():
    return [*targets.targets(), *target_generators.targets()]


def rules():
    return [*target_generators.rules(), *run.rules()]
