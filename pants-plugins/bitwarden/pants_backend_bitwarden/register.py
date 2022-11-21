"""
Pants backend for bitwarden.
"""

from pants_backend_bitwarden import targets
from pants_backend_bitwarden.goals import decrypt, run
from pants_backend_bitwarden.pants_ext.goals import decrypt as decrypt_goal
from pants_backend_bitwarden.util_rules import secret


def rules():
    return [
        *run.rules(),
        *secret.rules(),
        *decrypt.rules(),
        *decrypt_goal.rules(),
    ]


def target_types():
    return [
        *targets.targets(),
    ]
