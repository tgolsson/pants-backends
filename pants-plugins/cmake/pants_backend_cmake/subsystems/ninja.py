from __future__ import annotations

from pants.core.util_rules.external_tool import TemplatedExternalTool
from pants.engine.platform import Platform


class NinjaTool(TemplatedExternalTool):
    options_scope = "ninja"
    name = "ninja"
    help = "The Ninja CMake generates for. Only configure runs it; the replay never does."

    default_version = "v1.13.2"
    default_known_versions = [
        "v1.13.2|linux_x86_64|5749cbc4e668273514150a80e387a957f933c6ed3f5f11e03fb30955e2bbead6|134040",
        "v1.13.2|linux_arm64|fd2cacc8050a7f12a16a2e48f9e06fca5c14fc4c2bee2babb67b58be17a607fc|126256",
        "v1.13.2|macos_x86_64|c99048673aa765960a99cf10c6ddb9f1fad506099ff0a0e137ad8960a88f321b|314051",
        "v1.13.2|macos_arm64|c99048673aa765960a99cf10c6ddb9f1fad506099ff0a0e137ad8960a88f321b|314051",
    ]
    default_url_template = "https://github.com/ninja-build/ninja/releases/download/{version}/{platform}.zip"
    default_url_platform_mapping = {
        "linux_x86_64": "ninja-linux",
        "linux_arm64": "ninja-linux-aarch64",
        "macos_x86_64": "ninja-mac",
        "macos_arm64": "ninja-mac",
    }

    def generate_exe(self, plat: Platform) -> str:
        return "./ninja"


def rules():
    return [*NinjaTool.rules()]
