from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap

PLATFORM_MAPPING = {
    "macos_arm64": "macos-arm64",
    "macos_x86_64": "macos-amd64",
    "linux_x86_64": "linux-amd64",
}


class OdinTool(ExternalTool):
    options_scope = "odin"
    help = "Wrapper for Odin compiler and tools."

    default_version = "dev-2025-07"
    default_known_versions = [
        "dev-2025-07|macos_arm64 |25bd7b9276e3e73a296a4c0ed5ad33d50da177216b10f29d45ead86b5950c2ce|114366622",
        "dev-2025-07|macos_x86_64|8c1193f49fa7285a8d69a2b303fc7ac0e9751da7837788ac596fbde9a66270f9|122094700",
        "dev-2025-07|linux_x86_64|271a200a8c5428cdd9377b6116829b8ae70ffaaa639295acef83a5529264313b|106413093",
    ]

    skip = BoolOption(
        default=False,
        help=softwrap("""If true, don't use Odin when running `pants lint`."""),
    )

    tailor = BoolOption(
        default=True,
        help=softwrap("""If true, add `odin_sources` targets with the `tailor` goal."""),
        advanced=True,
    )

    def generate_url(self, plat: Platform) -> str:
        plat_str = PLATFORM_MAPPING[plat.value]
        base = "https://github.com/odin-lang/Odin/releases/download"
        return f"{base}/{self.version}/odin-{plat_str}-{self.version}.tar.gz"

    def generate_exe(self, plat: Platform) -> str:
        plat_str = PLATFORM_MAPPING[plat.value]
        return f"odin-{plat_str}-{self.version}/odin"


class OdinfmtTool(Subsystem):
    options_scope = "odinfmt"
    help = "Wrapper for odinfmt formatter from OLS repository."

    skip = BoolOption(
        default=False,
        help=softwrap("""If true, don't use odinfmt when running `pants fmt`."""),
    )
