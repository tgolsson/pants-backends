"""

"""


from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform


class BitwardenTool(ExternalTool):
    options_scope = "bitwarden"
    help = "Tool for bitwarden"

    default_version = "2022.10.0"
    default_known_versions = [
        "2022.10.0|linux_x86_64|3a47014de6842264b7d0cdd134e54c7b7e8461e7d353814a8b36f1f4ffc9d997|26038342",
        "2022.10.0|macos_x86_64|c604a09841435f6388fffc13cb6f772a777041ff2759d24c24e7e7c0687e62de|26225421",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_x86_64": "linux",
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
