from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption, StrListOption, StrOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class OciSubsystem(Subsystem):
    options_scope = "oci"
    help = "Generic options for the OCI subsystem."

    command_shell = StrListOption(
        default=["/bin/sh", "-c"],
        advanced=True,
        help="The default shell to use for container build commands.",
    )

    rootless = BoolOption(
        default=True,
        advanced=True,
        help=(
            "Whether to run in rootless mode, which changes behavior for some commands. Recommended on but"
            " may require more setup.."
        ),
    )

    uid_map = StrListOption(
        default=["0:1000:1", "1:100000:65536"],
        advanced=True,
        help="The UID map to use.",
    )

    gid_map = StrListOption(
        default=["0:1000:1", "1:100000:65536"],
        advanced=True,
        help="The GID map to use.",
    )

    empty_image_target = StrOption(
        default="empty",
        advanced=False,
        help="The name of the synthetic target for an empty base image.",
    )

    unsafe_tar_ignore_file_changed = BoolOption(
        default=False,
        advanced=True,
        help=softwrap("""
        Adds `--warning=no-file-changed` to tar calls.

        When working with large files, Pants will use symlinks instead of copying files into the
        sandboxes. Unfortunately, creating a symlink *modifies the target file*, which means
        that tar can detect that the file was linked into another sandbox and fail with  a "file changed"
        warning."""),
    )


class UmociTool(ExternalTool):
    options_scope = "umoci"
    help = "Umoci is used for manipulating OCI images."

    default_version = "v0.4.7"
    default_known_versions = [
        "nightly|linux_x86_64|8f32e43001a062e0fac53fc4cf8b48d659740d64c60395ec62b5680914c3a43d|9198496",
        "nightly|linux_arm64|f14fbe90f0dc575318929cf9361dd77918ec926ef27b068c9951a278e1d61865|8928657",
        "nightly|macos_x86_64|b49ab8c09b776e3e432bc2d46f79380d13c4d8f2dc0ab3c33eed4b9caac93201|9033392",
        "nightly|macos_arm64|b677454b102895ffa85e9e062d7113064683f4947b4316b26c83a641f9e07829|8906114",
        "v0.4.7|linux_x86_64|6abecdbe7ac96a8e48fdb73fb53f08d21d4dc5e040f7590d2ca5547b7f2b2e85|7499776",
    ]

    log = StrOption(
        default="warn",
        advanced=False,
        help="The log level for umoci.",
    )

    nightly_commit = StrOption(
        default="c6c4428ce046b92a541f6e0c89b9e00f758d83bb",
        advanced=True,
        help="The commit to use when running nighly releases.",
    )

    def generate_url(self, plat: Platform) -> str:
        if self.version == "nightly":
            repo = "https://github.com/tgolsson/umoci-binary"
            platform_mapping = {
                "linux_x86_64": "linux-amd64",
                "linux_arm64": "linux-arm64",
                "macos_x86_64": "darwin-amd64",
                "macos_arm64": "darwin-arm64",
            }
            plat_str = platform_mapping[plat.value]
            binary = f"umoci-{plat_str}"
            version = f"commit-{self.nightly_commit}"

        else:
            repo = "https://github.com/opencontainers/umoci"
            platform_mapping = {
                "linux_x86_64": "amd64",
            }
            plat_str = platform_mapping[plat.value]
            binary = f"umoci.{plat_str}"
            version = self.version

        return f"{repo}/releases/download/{version}/{binary}"

    def generate_exe(self, plat: Platform) -> str:
        if self.version == "nightly":
            platform_mapping = {
                "linux_x86_64": "linux-amd64",
                "linux_arm64": "linux-arm64",
                "macos_x86_64": "darwin-amd64",
                "macos_arm64": "darwin-arm64",
            }
            plat_str = platform_mapping[plat.value]
            return f"./umoci-{plat_str}"

        platform_mapping = {
            "linux_x86_64": "amd64",
        }
        plat_str = platform_mapping[plat.value]
        return f"./umoci.{plat_str}"


class SkopeoTool(ExternalTool):
    options_scope = "skopeo"
    help = "Tool for moving containers."

    default_version = "v1.13.3"
    default_known_versions = [
        "v1.13.3|linux_arm64|1f7726b020ff9bc931ce16caa13c29999738a231f1414028282cd8f8661eb747|35404961",
        "v1.13.3|linux_x86_64|65707992885b1a4a446af6342874749478a1af7e17ab3f4df8fb89509e8b1966|34276600",
        "v1.13.3|macos_x86_64|5433037c8f6d0db84cd9ff244320044654a7aa329cd7f0c0d04a68cffd89c836|33124304",
        "v1.13.3|macos_arm64|f57337d1695f804bcd8fa752c9d2784e8a0a2a405863740b8587d598825ef53a|32484706",
        "v1.9.2|linux_arm64 |1b7b4411c9723dbbdda4ae9dde23a33d8ab093b54c97d3323784b117d3e9413f|32542312",
        "v1.9.2|linux_x86_64|5c82f8fc2bcb2502cf7cdf9239f54468d52f5a2a8072893c75408b78173c4ba6|30920360",
        "v1.9.3|linux_arm64 |27c88183de036ebd4ffa5bc5211329666e3c40ac69c5d938bcdab9b9ec248fd4|30189956",
        "v1.9.3|linux_x86_64|6e00cf4661c081fb1d010ce60904dccb880788a52bf10de16a40f32082415a87|29390800",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "linux-arm64",
            "linux_x86_64": "linux-amd64",
            "macos_x86_64": "darwin-amd64",
            "macos_arm64": "darwin-arm64",
        }
        plat_str = platform_mapping[plat.value]
        if self.version in {"1.9.2", "1.9.3"}:
            return (
                f"https://github.com/lework/skopeo-binary/releases/download/{self.version}/skopeo-{plat_str}"
            )

        return f"https://github.com/tgolsson/skopeo-binary/releases/download/{self.version}/skopeo-{plat_str}"

    def generate_exe(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "linux-arm64",
            "linux_x86_64": "linux-amd64",
            "macos_arm64": "darwin-arm64",
            "macos_x86_64": "darwin-amd64",
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
        *SkopeoTool.rules(),
        *RuncTool.rules(),
        *UmociTool.rules(),
        *OciSubsystem.rules(),
    ]
