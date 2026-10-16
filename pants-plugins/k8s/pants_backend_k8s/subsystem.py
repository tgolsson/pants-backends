from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform


class KubernetesTool(ExternalTool):
    options_scope = "kubectl-tool"
    help = "Wrapper for kubectl."

    default_version = "v1.37.1"
    # Latest patches of non-EOL minors as of 2026-10-16. Drop 1.34 after 2026-10-27.
    # https://kubernetes.io/releases/
    default_known_versions = [
        "v1.37.1|linux_x86_64|65691ff77eb6fa44c908b77a1082c9f092c3b9733b5cefabec0d1104890e21a8|61886626",
        "v1.37.1|linux_arm64|ff749f4b78d9c4f1ec87307df9b50119ed819e2094aa9810cb9acffc3286c8c7|57868450",
        "v1.37.1|macos_x86_64|6851381c486ff6edd691623e3d65c87cb9a5b02887ff8fbbb38d8a031b748387|63190160",
        "v1.37.1|macos_arm64|fd65982c97ddad3106754b69ffa196d0e543aa591930ae52aed1adfb92f8c77f|59592530",
        "v1.36.5|linux_x86_64|33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e|59584674",
        "v1.36.5|linux_arm64|88fc1aca8fd0c1b44fe379f67c39bd6858bdfc50241dc55775f84d0f5b31da3a|55771298",
        "v1.36.5|macos_x86_64|03338828c3ba74c37bf5964573fbfbab132eb5bb15da2f821c25a4043d6eb3f0|60838864",
        "v1.36.5|macos_arm64|c5850a4a6b9469b26cce47c1ab456285f58e308283dcdf72b4ddda3db49b0ec0|57326034",
        "v1.35.9|linux_x86_64|3cfeaf80be482b435b0aa214aff6e0b2c312ee23c0ff20810c75517b6004c6eb|59121826",
        "v1.35.9|linux_arm64|39c98bca82875d9a9ddfb6f3c5ab17c3f78ea0801230683e538a67d5c053308d|55378082",
        "v1.35.9|macos_x86_64|51a130a5ea965314a41d27e840e96a7f4e4c13c508ab5fa0fd101ee4c1829990|60368000",
        "v1.35.9|macos_arm64|652548e6a9405266c5acd2719703bc8d36befa46e79e151a0d646904aa32c89b|56938210",
        "v1.34.12|linux_x86_64|90b7b9058ffeb5c10710bb1c73f541eaf426fb1b4e87df435db669de9a2564a2|61264034",
        "v1.34.12|linux_arm64|c819e3eb6a540d4f1e6aacaac85580bf0793bd1e7d05095a47407733ccf0cfb7|57409698",
        "v1.34.12|macos_x86_64|cefed789392b4d1f75cb81ee803d89c2bad68ec35414df127779a5557da00516|62556000",
        "v1.34.12|macos_arm64|0c37a655d8495312c90554b61b9ff8400077e743ae2dc07008a529667a750db9|59064866",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "macos_arm64": "darwin/arm64",
            "macos_x86_64": "darwin/amd64",
            "linux_x86_64": "linux/amd64",
            "linux_arm64": "linux/arm64",
        }
        plat_str = platform_mapping[plat.value]
        return f"https://dl.k8s.io/release/{self.version}/bin/{plat_str}/kubectl"
