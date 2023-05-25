import datetime
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import CreateDigest, Digest, Directory, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, rule

from pants_backend_oci.subsystem import OciSubsystem, UmociTool
from pants_backend_oci.util_rules.image_bundle import ImageBundle


@dataclass(frozen=True)
class UnpackedImageBundleRequest:
    bundle: ImageBundle


@dataclass(frozen=True)
class RepackedImageBundleRequest:
    command: str


@dataclass(frozen=True)
class UnpackedImageBundle:
    digest: Digest


@rule
async def make_unpack_process(
    request: UnpackedImageBundleRequest, tool: UmociTool, platform: Platform, oci: OciSubsystem
) -> Process:
    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))
    output_dir = await Get(Digest, CreateDigest([Directory("unpacked_image")]))
    input_digest = await Get(Digest, MergeDigests([request.bundle, umoci.digest, output_dir]))

    command = [
        f"{{chroot}}/{umoci.exe}",
        f"--log={tool.log}",
        "unpack",
        "--keep-dirlinks",
        "--image",
        "build:build",
        "unpacked_image",
    ]

    if oci.rootless:
        command.append("--rootless")
    for uid in oci.uid_map:
        command.append(f"--uid-map={uid}")

    for gid in oci.gid_map:
        command.append(f"--gid-map={gid}")

    return Process(
        tuple(command),
        description="Unpacking OCI bundle",
        input_digest=input_digest,
        #        output_directories=("unpacked_image",),
    )


@rule
async def make_repack_process(
    request: RepackedImageBundleRequest, tool: UmociTool, platform: Platform
) -> Process:
    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"

    command = (
        f"{{chroot}}/{umoci.exe}",
        f"--log={tool.log}",
        "repack",
        "--history.author=pants_backend_oci",
        f"--history.created_by='repack of {request.command}'",
        f"--history.comment='{request.command}'",
        f"--history.created={timestamp}",
        "--image",
        "build:build",
        "unpacked_image",
    )
    return Process(
        command,
        description="Repacking OCI bundle",
        output_directories=("build/",),
    )


def rules():
    return collect_rules()
