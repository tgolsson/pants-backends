"""
Pants backend for bitwarden.
"""

from pants_backend_bitwarden import targets
from pants_backend_bitwarden.goals import decrypt, run
from pants_backend_bitwarden.pants_ext import publish_secret_python, secret_request
from pants_backend_bitwarden.pants_ext.goals import decrypt as decrypt_goal
from pants_backend_bitwarden.util_rules import secret


def rules():
    return [
        *run.rules(),
        *secret.rules(),
        *decrypt.rules(),
        *decrypt_goal.rules(),
        *publish_secret_python.rules(),
        *secret_request.rules(),
    ]


def target_types():
    return [*targets.targets(), publish_secret_python.PythonDistributionWithSecret]
