from __future__ import annotations

from pants.option.option_types import SkipOption
from pants.option.subsystem import Subsystem


class OciTestSubsystem(Subsystem):
    options_scope = "oci-test"
    name = "OCI test subsystem"

    help = "Options for OCI container tests."

    skip = SkipOption("test")
