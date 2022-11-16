from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform


class KubernetesTool(ExternalTool):
    options_scope = "kubectl-tool"
    help = "Wrapper for kubectl."

    default_version = "v1.25.2"
    default_known_versions = [
        "v1.25.2|macos_arm64 |3c1e8b95cef4ff6e52d5f4b8c65b8d9d06b75f42d1cb40986c1d67729d82411a|10169842",  # noqa
        "v1.25.2|macos_x86_64|6fd57e78ed0c06b5bdd82750c5dc6d0f992a7b926d114fe94be46d7a7e32b63a|10496583",  # noqa
        "v1.25.2|linux_x86_64|8639f2b9c33d38910d706171ce3d25be9b19fc139d0e3d4627f38ce84f9040eb|45015040",  # noqa
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "macos_arm64": "darwin/arm64",
            "macos_x86_64": "darwin/amd64",
            "linux_x86_64": "linux/amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"https://dl.k8s.io/release/{self.version}/bin/{plat_str}/kubectl"
