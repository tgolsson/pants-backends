from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption
from pants.util.strutil import softwrap


class KustomizeTool(ExternalTool):
    options_scope = "kustomize-tool"
    help = "Wrapper for kustomize."

    default_version = "v4.5.7"
    default_known_versions = [
        "v4.5.7|macos_arm64 |3c1e8b95cef4ff6e52d5f4b8c65b8d9d06b75f42d1cb40986c1d67729d82411a|10169842",
        "v4.5.7|macos_x86_64|6fd57e78ed0c06b5bdd82750c5dc6d0f992a7b926d114fe94be46d7a7e32b63a|10496583",
        "v4.5.7|linux_arm64 |65665b39297cc73c13918f05bbe8450d17556f0acd16242a339271e14861df67|5291095",
        "v4.5.7|linux_x86_64|701e3c4bfa14e4c520d481fdf7131f902531bfc002cb5062dcf31263a09c70c9|5757484",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "macos_arm64": "darwin_arm64",
            "macos_x86_64": "darwin_amd64",
            "linux_arm64": "linux_arm64",
            "linux_x86_64": "linux_amd64",
            "linux_arm64": "linux_arm64",
            "linux_x86_64": "linux_amd64",
        }
        plat_str = platform_mapping[plat.value]
        return (
            f"https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/{self.version}/"
            f"kustomize_{self.version}_{plat_str}.tar.gz"
        )

    def generate_exe(self, _: Platform) -> str:
        return f"./kustomize"
