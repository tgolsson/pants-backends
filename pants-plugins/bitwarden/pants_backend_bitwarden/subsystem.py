""" """

from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform


class BitwardenTool(ExternalTool):
    options_scope = "bitwarden"
    help = "Tool for bitwarden"

    default_version = "2026.10.0"
    default_known_versions = [
        "2026.10.0|linux_x86_64|269b67559caab860d1737f7554b32bdb8be9285d5689fee55193c137a0df9973|45806876",
        "2026.10.0|linux_arm64|2fe9d8a18dc4c35ea2f9e3e42a4021f14f129ef47e80b6cea1e3b34176f1583a|44318274",
        "2026.10.0|macos_x86_64|39602a597ca63127c22ee3844546f3fe06079e4f67be7b9a7ea4e30ede8721d0|44139200",
        "2026.10.0|macos_arm64|062c880508678b8d936ab2522a3a1e92d60f52b5c534e71406e9b47329e95207|41866176",
        "2026.9.1|linux_x86_64|84289eaf59d691f04242605112f21b00e8dba65acdedbc60dde001a2860f2097|45654895",
        "2026.9.1|linux_arm64|c451c773378a6f1f041ea26568a897b285a8656bee16a16e4914ce9e40fb7c79|44164116",
        "2026.9.1|macos_x86_64|7996558e562a7e5d1ef167f625e4dfde0230fe87246e01caf647aff5501ca658|43980370",
        "2026.9.1|macos_arm64|9f52edf5fb855e277315c1106f1bae326a87c41b425a4c0e00fd1440e77fa0a6|41705648",
        "2023.10.0|linux_x86_64|25b3376c7b215b9850844a14a15daa8b88817445acd0af12f5f2942606ef108a|32871398",
        "2023.10.0|macos_x86_64|c129b1806bb00806676298198fb54d540561e2c06d66016f0836e31b1bd65e72|33786559",
        "2022.10.0|linux_x86_64|3a47014de6842264b7d0cdd134e54c7b7e8461e7d353814a8b36f1f4ffc9d997|26038342",
        "2022.10.0|macos_x86_64|c604a09841435f6388fffc13cb6f772a777041ff2759d24c24e7e7c0687e62de|26225421",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_x86_64": "linux",
            "linux_arm64": "linux-arm64",
            "macos_x86_64": "macos",
            "macos_arm64": "macos-arm64",
        }
        platform = platform_mapping[plat.value]
        base = "https://github.com/bitwarden/clients/releases/download"
        return f"{base}/cli-v{self.version}/bw-{platform}-{self.version}.zip"

    def generate_exe(self, _: Platform) -> str:
        return "./bw"


def rules():
    return [
        *BitwardenTool.rules(),
    ]
