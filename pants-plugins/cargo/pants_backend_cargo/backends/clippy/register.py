from pants_backend_cargo.backends.clippy import subsystem
from pants_backend_cargo.backends.clippy.goals import lint


def rules():
    return [*subsystem.rules(), *lint.rules()]
