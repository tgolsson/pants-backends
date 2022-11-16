from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.engine.rules import SubsystemRule


class UmociTool(ExternalTool):
    options_scope = "umoci"
    help = "Wrapper for kustomize."

    default_version = "v0.4.7"
    default_known_versions = [
        "v0.4.7|linux_x86_64|6abecdbe7ac96a8e48fdb73fb53f08d21d4dc5e040f7590d2ca5547b7f2b2e85|7499776",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_x86_64": "amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"https://github.com/opencontainers/umoci/releases/download/{self.version}/umoci.{plat_str}"

    def generate_exe(self, plat: Platform) -> str:

        platform_mapping = {
            "linux_x86_64": "amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"./umoci.{plat_str}"


class SkopeoTool(ExternalTool):
    options_scope = "skopeo"
    help = "Wrapper for kustomize."

    default_version = "v1.9.3"
    default_known_versions = [
        "v1.9.2|linux_arm64 |1b7b4411c9723dbbdda4ae9dde23a33d8ab093b54c97d3323784b117d3e9413f|32542312",
        "v1.9.2|linux_x86_64|5c82f8fc2bcb2502cf7cdf9239f54468d52f5a2a8072893c75408b78173c4ba6|30920360",
        "v1.9.3|linux_arm64 |27c88183de036ebd4ffa5bc5211329666e3c40ac69c5d938bcdab9b9ec248fd4|30189956",
        "v1.9.3|linux_x86_64|6e00cf4661c081fb1d010ce60904dccb880788a52bf10de16a40f32082415a87|29390800",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "linux-arm64",
            "linux_x86_64": "linux-amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"https://github.com/lework/skopeo-binary/releases/download/{self.version}/skopeo-{plat_str}"

    def generate_exe(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "linux-arm64",
            "linux_x86_64": "linux-amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"./skopeo-{plat_str}"


class RuncTool(ExternalTool):
    options_scope = "runc"
    help = "Tool for executing OCI containers."

    default_version = "v1.1.4"
    default_known_versions = [
        "v1.1.4|linux_x86_64|db772be63147a4e747b4fe286c7c16a2edc4a8458bd3092ea46aaee77750e8ce|9431456",
        "v1.1.4|linux_arm64 |dbb71e737eaef454a406ce21fd021bd8f1b35afb7635016745992bbd7c17a223|9061960",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "arm64",
            "linux_x86_64": "amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"https://github.com/opencontainers/runc/releases/download/{self.version}/runc.{plat_str}"

    def generate_exe(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "arm64",
            "linux_x86_64": "amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"./runc.{plat_str}"


def rules():
    return [
        SubsystemRule(SkopeoTool),
        SubsystemRule(RuncTool),
        SubsystemRule(UmociTool),
    ]
