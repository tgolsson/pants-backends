"""

"""

from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform

_mdbook_platform_mapping = {
    "linux_x86_64": "x86_64-unknown-linux-gnu",
    "linux_arm64": "aarch64-unknown-linux-musl",
    "macos_x86_64": "x86_64-apple-darwin",
}


class MdBookTool(ExternalTool):
    options_scope = "mdbook"
    help = "The MdBook tool used to build static webpages."

    default_version = "v0.4.35"
    default_known_versions = [
        "v0.4.35|macos_x86_64|ca3281c2b5437a1ccd9079ed8121b3dd97c49be74dae32ea803b540a38c334bb|4701269",
        "v0.4.35|linux_x86_64|4ef777bfcb3fd01687deed990372a6eb5e125f79b592014b0ac09b61595f0b34|5348678",
        "v0.4.35|linux_arm64 |359af01b77fbd6bf6243a3f2b2491a37b5480bbb2674eb2d94f91354253b34f4|5442606",
        "v0.4.21|macos_x86_64|c1f3e8235f8b3128f6020397061c97444007e090ac42358402d3f25eee8499bd|4292102",
        "v0.4.21|linux_x86_64|ec3c978a255b444987fd6e0805147f4ea75d221f68c6e27dbf3e8a28aba166b7|4861607",
    ]

    def generate_url(self, plat: Platform) -> str:
        plat_str = _mdbook_platform_mapping[plat.value]
        base = "https://github.com/rust-lang/mdBook/releases/download"
        return f"{base}/{self.version}/mdbook-{self.version}-{plat_str}.tar.gz"

    def generate_exe(self, plat: Platform) -> str:
        return "./mdbook"


def rules():
    return [
        *MdBookTool.rules(),
    ]
