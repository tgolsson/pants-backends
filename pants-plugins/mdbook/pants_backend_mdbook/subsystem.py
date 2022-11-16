"""

"""
from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.engine.rules import SubsystemRule

_mdbook_platform_mapping = {
    "linux_x86_64": "x86_64-unknown-linux-gnu",
    "macos_x86_64": "x86_64-apple-darwin",
}


class MdBookTool(ExternalTool):
    options_scope = "mdbook"
    help = "The MdBook tool used to build static webpages."

    default_version = "v0.4.21"
    default_known_versions = [
        "v0.4.21|macos_x86_64|c1f3e8235f8b3128f6020397061c97444007e090ac42358402d3f25eee8499bd|4292102",
        "v0.4.21|linux_x86_64|ec3c978a255b444987fd6e0805147f4ea75d221f68c6e27dbf3e8a28aba166b7|4861607",
    ]

    def generate_url(self, plat: Platform) -> str:
        plat_str = _mdbook_platform_mapping[plat.value]
        return f"https://github.com/rust-lang/mdBook/releases/download/{self.version}/mdbook-{self.version}-{plat_str}.tar.gz"

    def generate_exe(self, plat: Platform) -> str:
        return "./mdbook"


def rules():
    return [
        SubsystemRule(MdBookTool),
    ]
