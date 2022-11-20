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
        "2022.10.0|linux_x86_64|3a47014de6842264b7d0cdd134e54c7b7e8461e7d353814a8b36f1f4ffc9d997|26038342"
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_x86_64": "linux",
        }
        platform = platform_mapping[plat.value]
        return f"https://github.com/bitwarden/clients/releases/download/cli-v{self.version}/bw-{platform}-{self.version}.zip"

    def generate_exe(self, _: Platform) -> str:
        return "./bw"
