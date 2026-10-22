from __future__ import annotations

from pants.core.util_rules.external_tool import TemplatedExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import StrOption
from pants.util.strutil import softwrap

_PLATFORM_MAPPING = {
    "linux_x86_64": "linux-x86_64",
    "linux_arm64": "linux-aarch64",
    "macos_x86_64": "macos-universal",
    "macos_arm64": "macos-universal",
}


class CMakeTool(TemplatedExternalTool):
    options_scope = "cmake"
    name = "cmake"
    help = softwrap(
        """
        The CMake used to configure `cmake_project` targets, and the host compilers CMake is
        pointed at.
        """
    )

    default_version = "3.31.12"
    default_known_versions = [
        "3.31.12|linux_x86_64|0dc2e9a6860f06bf10bd8fadc03e35d9eeb4df46e33763a7e480e987758f385c|55005786",
        "3.31.12|linux_arm64|83f8fd91d2038a56556e1400390fcfe42f79602940c494f6c6f1cdae7f9e7f40|56324822",
        "3.31.12|macos_x86_64|799af7fd545db9bf1b9cfe72f8095880e727a2d4e0df0e3dffc3bc7b95c2d3b0|79954716",
        "3.31.12|macos_arm64|799af7fd545db9bf1b9cfe72f8095880e727a2d4e0df0e3dffc3bc7b95c2d3b0|79954716",
    ]
    default_url_template = (
        "https://github.com/Kitware/CMake/releases/download/v{version}/cmake-{version}-{platform}.tar.gz"
    )
    default_url_platform_mapping = _PLATFORM_MAPPING

    c_compiler = StrOption(
        default="gcc",
        help="The C compiler, a name resolved on the system binary path or an absolute path.",
    )
    cxx_compiler = StrOption(
        default="g++",
        help="The C++ compiler, a name resolved on the system binary path or an absolute path.",
    )

    def generate_exe(self, plat: Platform) -> str:
        base = f"cmake-{self.version}-{_PLATFORM_MAPPING[plat.value]}"
        if plat.is_macos:
            return f"{base}/CMake.app/Contents/bin/cmake"
        return f"{base}/bin/cmake"


def rules():
    return [*CMakeTool.rules()]
