from pants.option.option_types import DictOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class DebianPackagesSubsystem(Subsystem):
    options_scope = "debian-packages"
    help = "Options for installing Debian packages."

    resolves = DictOption[str](
        default={},
        help=softwrap(
            f"""
            A mapping from debian package resolve to lockfile path.
            """
        ),
        advanced=True,
    )


def rules():
    return DebianPackagesSubsystem.rules()
