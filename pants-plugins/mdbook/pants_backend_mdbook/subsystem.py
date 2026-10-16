""" """

from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform

_mdbook_platform_mapping = {
    "linux_x86_64": "x86_64-unknown-linux-gnu",
    "linux_arm64": "aarch64-unknown-linux-musl",
    "macos_x86_64": "x86_64-apple-darwin",
    "macos_arm64": "aarch64-apple-darwin",
}


class MdBookTool(ExternalTool):
    options_scope = "mdbook"
    help = "The MdBook tool used to build static webpages."

    default_version = "v0.5.4"
    default_known_versions = [
        "v0.5.4|linux_x86_64|3f28de05dafca9d0f2eab99c662116b0e37b89b1d96a08f8f430b9eeae958cd7|4822940",
        "v0.5.4|linux_arm64|753e5c5c363ee8a56972344dcf91466f005a51db84a7aeffe427ae3ef83d6d44|4758117",
        "v0.5.4|macos_x86_64|a47d7bf0d5d670cff9ee6cce95537cbeb62dc10704d9e7131ffbd13e2b59a5de|4609728",
        "v0.5.4|macos_arm64|03e8a6d8b13a2971e0b3280affd03b388373c1485e26f73407c3a76b0b1838df|4420902",
        "v0.4.52|linux_x86_64|c0b903f01dd8f4edc644372ad2b80b1fdddd12552d37b6a098657cbd8eddd768|4658310",
        "v0.4.52|linux_arm64|7273dda980915a1e2f114d63d432aa6284551e37f0358e3ce7653d1e49e6fa3f|4534216",
        "v0.4.52|macos_x86_64|4768a2a3cd6bff139feffd855dd8fb82ee356ecf822ae12ed9abcbde0633205b|4432826",
        "v0.4.52|macos_arm64|4aee6a8ff54dc59fbe9bcf1c38b8f58583fba63535dff7a5ec8205f0cffc3fd4|4249801",
        "v0.4.35|macos_arm64 |ca3281c2b5437a1ccd9079ed8121b3dd97c49be74dae32ea803b540a38c334bb|4701269",
        "v0.4.35|macos_x86_64|ca3281c2b5437a1ccd9079ed8121b3dd97c49be74dae32ea803b540a38c334bb|4701269",
        "v0.4.35|linux_x86_64|4ef777bfcb3fd01687deed990372a6eb5e125f79b592014b0ac09b61595f0b34|5348678",
        "v0.4.35|linux_arm64 |359af01b77fbd6bf6243a3f2b2491a37b5480bbb2674eb2d94f91354253b34f4|5442606",
        "v0.4.21|macos_x86_64|c1f3e8235f8b3128f6020397061c97444007e090ac42358402d3f25eee8499bd|4292102",
        "v0.4.21|linux_x86_64|ec3c978a255b444987fd6e0805147f4ea75d221f68c6e27dbf3e8a28aba166b7|4861607",
    ]

    def generate_url(self, plat: Platform) -> str:
        plat_str = _mdbook_platform_mapping[plat.value]
        # Older releases did not publish native Apple Silicon archives.
        if plat == Platform.macos_arm64 and self.version in {"v0.4.21", "v0.4.35"}:
            plat_str = "x86_64-apple-darwin"
        base = "https://github.com/rust-lang/mdBook/releases/download"
        return f"{base}/{self.version}/mdbook-{self.version}-{plat_str}.tar.gz"

    def generate_exe(self, plat: Platform) -> str:
        return "./mdbook"


def rules():
    return [
        *MdBookTool.rules(),
    ]
