"""

Pants backend for building distroless-style images from resolved Debian packages.
"""

from pants_backend_distroless import lockfile, subsystem, target_generation
from pants_backend_distroless import target_types as distroless_target_types


def rules():
    return [
        *lockfile.rules(),
        *subsystem.rules(),
        *target_generation.rules(),
    ]


def target_types():
    return distroless_target_types.targets()
