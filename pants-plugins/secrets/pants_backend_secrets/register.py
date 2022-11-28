"""

Pants backend for handling secrets.
"""

from pants_backend_secrets import (
    env_secret_provider,
    publish_secret_python,
    secret_request,
    targets,
)
from pants_backend_secrets.goals import decrypt


def rules():
    return [
        *decrypt.rules(),
        *publish_secret_python.rules(),
        *secret_request.rules(),
        *env_secret_provider.rules(),
    ]


def target_types():
    return [*targets.targets(), publish_secret_python.PythonDistributionWithSecret]
