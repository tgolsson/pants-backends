from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, rule

from pants_backend_oci.subsystem import UmociTool
from pants_backend_oci.util_rules.image_bundle import ImageBundle


@dataclass(frozen=True)
class UnpackedImageBundleRequest:
    bundle: ImageBundle


@dataclass(frozen=True)
class UnpackedImageBundle:
    digest: Digest


@rule
async def make_unpack_process(
    request: UnpackedImageBundleRequest, tool: UmociTool, platform: Platform
) -> Process:
    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

    input_digest = await Get(Digest, MergeDigests([request.bundle, umoci.digest]))

    command = (
        f"{{chroot}}/{umoci.exe}",
        "unpack",
        "--rootless",
        "--image",
        "build:build",
        "unpacked_image",
    )

    return Process(
        command,
        description="Unpacking OCI bundle",
        input_digest=input_digest,
        output_directories=("unpacked_image",),
    )


def rules():
    return collect_rules()
