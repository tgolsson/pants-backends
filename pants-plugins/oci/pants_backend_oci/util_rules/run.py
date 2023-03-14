from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import BashBinary, CatBinary, CpBinary, MkdirBinary
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule

from pants_backend_oci.subsystem import RuncTool, UmociTool
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.jq import JqBinary, JqBinaryRequest
from pants_backend_oci.util_rules.unpack import (
    RepackedImageBundleRequest,
    UnpackedImageBundleRequest,
)


@dataclass(frozen=True)
class RunContainerRequest:
    bundle: ImageBundle
    command: tuple[str]

    repack: bool = False


@rule
async def run_in_container(
    request: RunContainerRequest,
    umoci: UmociTool,
    runc: RuncTool,
    platform: Platform,
    bash: BashBinary,
    cat: CatBinary,
    cp: CpBinary,
    mkdir: MkdirBinary,
) -> ProcessResult:
    download_runc_tool = Get(
        DownloadedExternalTool, ExternalToolRequest, runc.get_request(platform)
    )

    tool, rundir, jq = await MultiGet(
        download_runc_tool,
        Get(Digest, CreateDigest([Directory("runspace")])),
        Get(JqBinary, JqBinaryRequest()),
    )

    packed_image_process = await Get(Process, UnpackedImageBundleRequest(request.bundle.digest))
    # name = str(request.target.address).replace("/", "_").replace(":", "_").replace("#", "_")
    script = dedent(
        f"""
        ROOT=`pwd`
        for arg in "$@"; do
            echo "$arg" | {jq.path} -R --argjson config "$({cat.path} $ROOT/unpacked_image/config.json)" '
                    .  as $arg  | $config | .process.args += [$arg]
                ' > "$ROOT/unpacked_image/config.json"
        done
        set -x
        {cat.path} <<< $({jq.path} '
            .process.terminal = false
        ' "$ROOT/unpacked_image/config.json") > "$ROOT/unpacked_image/config.json"
        echo $ROOT
        `pwd`/{tool.exe} --root runspace --rootless true run -b unpacked_image pants.runc
    """
    )
    script_digest = await Get(Digest, CreateDigest([FileContent("run.sh", script.encode("utf-8"))]))
    (input_digest,) = await MultiGet(
        Get(Digest, MergeDigests((rundir, tool.digest, script_digest))),
    )

    steps = [
        packed_image_process,
        Process(
            (bash.path, "{chroot}/run.sh", f"{request.command}"),
            description="Running container build command",
            input_digest=input_digest,
        ),
    ]

    if request.repack:
        steps.append(await Get(Process, RepackedImageBundleRequest()))

    return await Get(
        ProcessResult,
        FusedProcess(tuple(steps)),
    )


def rules():
    return collect_rules()
