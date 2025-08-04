from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption
from pants.util.strutil import softwrap


class OdinTool(ExternalTool):
    options_scope = "odin-tool"
    help = "Wrapper for Odin compiler and tools."

    default_version = "v0.13.0"
    default_known_versions = [
        "v0.13.0|macos_arm64 |e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|0",
        "v0.13.0|macos_x86_64|e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|0",
        "v0.13.0|linux_arm64 |e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|0",
        "v0.13.0|linux_x86_64|e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|0",
    ]

    skip = BoolOption(
        default=False,
        help=softwrap("""If true, don't use Odin when running `pants lint`."""),
    )

    tailor = BoolOption(
        default=True,
        help=softwrap("""If true, add `odin_sources` targets with the `tailor` goal."""),
        advanced=True,
    )

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "macos_arm64": "darwin-arm64",
            "macos_x86_64": "darwin-amd64",
            "linux_arm64": "linux-arm64",
            "linux_x86_64": "linux-amd64",
        }
        plat_str = platform_mapping[plat.value]
        base = "https://github.com/odin-lang/Odin/releases/download"
        return f"{base}/{self.version}/odin-{self.version}-{plat_str}.tar.gz"

    def generate_exe(self, _: Platform) -> str:
        return "./odin"