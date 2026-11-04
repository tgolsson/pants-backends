from pants.option.option_types import ArgsListOption, SkipOption
from pants.option.subsystem import Subsystem


class ClippySubsystem(Subsystem):
    options_scope = "clippy"
    name = "clippy"
    help = "Settings for clippy."

    skip = SkipOption("lint")
    args = ArgsListOption(example="-- -D warnings")


def rules():
    return ClippySubsystem.rules()
