from pants_backend_k8s import target_generators
from pants_backend_k8s import target_types as targets
from pants_backend_k8s.goals import run
from pants_backend_k8s.util import kubeconfig


def target_types():
    return [*targets.targets(), *target_generators.targets()]


def rules():
    return [*kubeconfig.rules(), *target_generators.rules(), *run.rules()]
