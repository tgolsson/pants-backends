from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption, SkipOption, StrListOption, StrOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class OciSubsystem(Subsystem):
    options_scope = "oci"
    help = "Generic options for the OCI subsystem."

    command_shell = StrListOption(
        default=["/bin/sh", "-c"],
        advanced=True,
        help=(
            "The shell that runs string `commands=` in scripted build steps. It must exist in the base"
            " image. A list of strings runs without it."
        ),
    )

    empty_image_target = StrOption(
        default="empty",
        advanced=False,
        help="The name of the synthetic target for an empty base image.",
    )

    builder_pbs_release = StrOption(
        default="20261009",
        advanced=True,
        help=softwrap("""
        The python-build-standalone release `oci-builder` takes its Python from, such as
        `20261009`. Layer compression depends on that Python's zlib, so changing it may change
        layer digests."""),
    )

    builder_python_version = StrOption(
        default="3.11.17",
        advanced=True,
        help=softwrap("""
        The Python version, `<major>.<minor>.<patch>`, `oci-builder` runs on. It must be in
        `[oci].builder_pbs_release`. Layer compression depends on its zlib, so changing it may
        change layer digests."""),
    )

    unsafe_tar_ignore_file_changed = BoolOption(
        default=False,
        advanced=True,
        help=softwrap("""
        Deprecated, and has no effect: layers are written by the plugin's Python instead of `tar`,
        so a file Pants hardlinks into another sandbox mid-read no longer fails the build. This
        option will be removed in a future release."""),
    )


class DebLayerOverwrites(Subsystem):
    options_scope = "deb-layer-overwrites"
    name = "deb-layer-overwrites"
    help = softwrap("""
    A lint over `oci_image_build` targets that fails when an `oci_deb_layer` overwrites regular
    files from the layers below it, such as its base image, with different content. That mixes
    package versions, as when the base was built from a different archive state than the
    snapshot the layer's packages come from. Identical files aren't reported.
    """)

    skip = SkipOption("lint")


class CraneTool(ExternalTool):
    options_scope = "crane"
    help = "Crane is used for interacting with remote OCI registries."

    # Variables crane needs to find registry credentials: docker config, credential
    # helpers on PATH, and podman auth files.
    registry_env_vars = (
        "HOME",
        "PATH",
        "DOCKER_CONFIG",
        "REGISTRY_AUTH_FILE",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
    )

    default_version = "v0.22.1"
    default_known_versions = [
        "v0.22.1|linux_x86_64|0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0|16496784",
        "v0.22.1|linux_arm64|898c0cff975f898a33e8c4580bdafb0e7c02c7faa33374e946762f97c4ab7110|14831376",
        "v0.22.1|macos_x86_64|6fedd06a648c11335f0e8b9547e4783002c30e9336047fec292942cd518bb799|16785187",
        "v0.22.1|macos_arm64|2231fc8df8806d20d680ff1225db44e095a55dd6ac1ae8eced4faf4b278b78fb|15441389",
    ]

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "Linux_arm64",
            "linux_x86_64": "Linux_x86_64",
            "macos_arm64": "Darwin_arm64",
            "macos_x86_64": "Darwin_x86_64",
        }
        plat_str = platform_mapping[plat.value]
        base = "https://github.com/google/go-containerregistry/releases/download"
        return f"{base}/{self.version}/go-containerregistry_{plat_str}.tar.gz"

    def generate_exe(self, _: Platform) -> str:
        return "./crane"


class ZstdTool(ExternalTool):
    options_scope = "zstd"
    help = softwrap("""
    Zstandard, used to decompress `.deb` packages whose members are zstd-compressed, as Ubuntu's
    are. The default binaries are the static builds from https://github.com/aspect-build/zstd-prebuilt.
    """)

    default_version = "v1.5.6-bcr2"
    default_known_versions = [
        "v1.5.6-bcr2|linux_x86_64|190c196b46883a234238d9c52a211c1494df79bb170fbe30c6fc22639d5d4731|1147128",
        "v1.5.6-bcr2|linux_arm64|4fa2386a975c2120f91f3103bd4268a19a341cb9063417f3829018e94f28c8c6|940152",
        "v1.5.6-bcr2|macos_x86_64|d81be8618553d7d658e09da669ffd68ec76d8095161a590c9b8a1a99fce67ca8|1127528",
        "v1.5.6-bcr2|macos_arm64|9848858f7332effb071818f282b3ac3052085e3dafa9ae09f0060b7e211a80dc|976184",
    ]

    def generate_url(self, plat: Platform) -> str:
        binary = self.generate_exe(plat).removeprefix("./")
        return f"https://github.com/aspect-build/zstd-prebuilt/releases/download/{self.version}/{binary}"

    def generate_exe(self, plat: Platform) -> str:
        platform_mapping = {
            "linux_arm64": "linux_arm64",
            "linux_x86_64": "linux_amd64",
            "macos_arm64": "darwin_arm64",
            "macos_x86_64": "darwin_amd64",
        }
        return f"./zstd_{platform_mapping[plat.value]}"


def rules():
    return [
        *CraneTool.rules(),
        *ZstdTool.rules(),
        *OciSubsystem.rules(),
        *DebLayerOverwrites.rules(),
    ]
