"""

"""

from pants_backend_mdbook.util_rules import build, prepare


def rules():
    return [
        *build.rules(),
        *prepare.rules(),
    ]
