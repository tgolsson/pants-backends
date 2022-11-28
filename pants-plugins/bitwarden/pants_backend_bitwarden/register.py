"""
Pants backend for bitwarden.
"""

from pants_backend_bitwarden import targets
from pants_backend_bitwarden.goals import decrypt
from pants_backend_bitwarden.util_rules import secret


def rules():
    return [
        *secret.rules(),
        *decrypt.rules(),
    ]


def target_types():
    return targets.targets()
