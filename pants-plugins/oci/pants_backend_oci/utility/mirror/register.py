from . import targets
from .goals import package, publish


def rules():
    return [*targets.rules(), *publish.rules(), *package.rules()]


def target_types():
    return targets.targets()
