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

    default_version = "v0.6.0"
    default_known_versions = [
        "v0.6.0|macos_x86_64|01ddbeeb491409e18c5e909f9f50bbba545c5feba4ce3ca87e256e81a2bca201|8724016",
        "v0.6.0|macos_arm64|5e85c642ebefcaa73eb035ebe319c7a62a83272a7d2b50c91c8b9315857b0f85|8181250",
        "v0.6.0|linux_x86_64|b51c267ec394499e42c6fde47f240b7b7dba57ea49df0b5acd304378b82a3b71|7946424",
        "v0.6.0|linux_arm64|5cfd17f2e7a4bcf9ed67ea1b955ca893d200349b9ce6a3d3707dba415f458a1f|7536824",
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
        help="The commit to use when running nightly releases.",
    )

    def generate_url(self, plat: Platform) -> str:
        if self.version == "nightly":
            repo = "https://github.com/tgolsson/umoci-binary"
            version = f"commit-{self.nightly_commit}"
        elif plat.value.startswith("macos_"):
            # Upstream publishes Linux binaries only.
            repo = "https://github.com/tgolsson/umoci-binary"
            version = self.version
        else:
            repo = "https://github.com/opencontainers/umoci"
            version = self.version
        binary = self.generate_exe(plat).removeprefix("./")
        return f"{repo}/releases/download/{version}/{binary}"

    def generate_exe(self, plat: Platform) -> str:
        arch = "arm64" if plat in {Platform.linux_arm64, Platform.macos_arm64} else "amd64"
        if self.version == "nightly" or plat.value.startswith("macos_"):
            os = "darwin" if plat.value.startswith("macos_") else "linux"
            return f"./umoci-{os}-{arch}"
        # The official release filename changed in 0.5.
        return f"./umoci.{arch}" if self.version == "v0.4.7" else f"./umoci.linux.{arch}"


class SkopeoTool(ExternalTool):
    options_scope = "skopeo"
    help = "Tool for moving containers."

    default_version = "v1.24.1"
    default_known_versions = [
        "v1.24.1|linux_x86_64|067d461564e606b4f54f30453a9f21a53aabc4f1bb0acd5193ffff5b1aececbc|43205536",
        "v1.24.1|linux_arm64|a7e32a8f39110a55e0230e0f27bf50f1b2e4f36841793b07a7ba711877e63020|36859900",
        "v1.24.1|macos_x86_64|44b1d962439819968ea4a9947627e9f49b2d3fed47d01ebeb2597bd88ad79512|35991728",
        "v1.24.1|macos_arm64|ba11a5e3f96a120345ead3d74774c3258473db39bcf42cc684f2a0cac281a9f9|34040002",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "linux-arm64",
            "linux_x86_64": "linux-amd64",
            "macos_x86_64": "darwin-amd64",
            "macos_arm64": "darwin-arm64",
        }
        plat_str = platform_mapping[plat.value]
        return f"https://github.com/lework/skopeo-binary/releases/download/{self.version}/skopeo-{plat_str}"

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

    default_version = "v1.5.2"
    default_known_versions = [
        "v1.5.2|linux_x86_64|599f6f94ff8c5057241eff0d54c3c74f95c34935b6457b33fe545defc61e9488|10866512",
        "v1.5.2|linux_arm64|d10ecae898361832a059be2089bab92d158aec54661b18ed7346ed79628b46b0|10091904",
        "v1.4.3|linux_x86_64|f6ae8efc0fa40079e1475e97cbe9d1bd3f106a28d6af78a11d9f1bd565515e60|12245392",
        "v1.4.3|linux_arm64|633301e2e32f8a5ad54031aab4901eb00308bec677dd15faa2751e8f9dab5ca4|11499552",
        "v1.3.6|linux_x86_64|3f3921dbbee7723e9868f97e88e51ffc910206e3ba55646e74d93d24ea76023c|13085848",
        "v1.3.6|linux_arm64|6be27a061ef89bb14c6cb495405bba80f46cea213b8f79378a6086b1bac6b747|12126664",
        "v1.2.9|linux_x86_64|b21fda1d5a1510b19c3f6fb43ecb3a92887654a9d0c2b64e84b8b08f24ff0e30|12702296",
        "v1.2.9|linux_arm64|c155ae4716480be73a1c3bbedfa23df179800ba39ca83eb59f5b1b9b72a7ecbd|11862152",
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
