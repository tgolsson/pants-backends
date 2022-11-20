"""
Pants backend for bitwarden.
"""

from pants_backend_bitwarden import targets
from pants_backend_bitwarden.goals import run


def rules():
    return [
        *run.rules(),
    ]


def target_types():
    return [
        *targets.targets(),
    ]
