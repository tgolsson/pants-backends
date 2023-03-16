"""

"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from pants.core.goals.run import RunDebugAdapterRequest, RunFieldSet, RunRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import (
    SEARCH_PATHS,
    BinaryPath,
    BinaryPathRequest,
    BinaryPaths,
    BinaryPathTest,
    MkdirBinary,
    MvBinary,
)
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import Target, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule

from pants_backend_oci.subsystem import RuncTool
from pants_backend_oci.target_types import ImageRepository, ImageRunTty
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundleRequest,
)
from pants_backend_oci.util_rules.unpack import UnpackedImageBundleRequest


@dataclass(frozen=True)
class RunImageBundleCommand(RunFieldSet):
    required_fields = (ImageRepository,)

    repository: ImageRepository
    run_tty: ImageRunTty


@dataclass(frozen=True)
class RunImageBundleProcessRequest:
    target: Target


class JqBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class JqBinaryRequest:
    pass


@rule
async def find_jq_wrapper(_: JqBinaryRequest, jq_binary: JqBinary) -> JqBinary:
    return jq_binary


@rule(desc="Finding the `jq` binary")
async def find_jq() -> JqBinary:
    request = BinaryPathRequest(
        binary_name="jq", search_path=SEARCH_PATHS, test=BinaryPathTest(args=["--version"])
    )
    paths = await Get(BinaryPaths, BinaryPathRequest, request)
    first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
    return JqBinary(first_path.path, first_path.fingerprint)


@rule
async def prepare_run_image_bundle(
    request: RunImageBundleProcessRequest,
    tool: RuncTool,
    mkdir_binary: MkdirBinary,
    mv: MvBinary,
    platform: Platform,
) -> Process:
    download_runc_tool = Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.target.address, description_of_origin="package_oci_image"),
    )
    target = wrapped_target.target
    bundle_request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(target))
    image = Get(FallibleImageBundle, FallibleImageBundleRequest, bundle_request.request)

    tool, image, rundir, jq = await MultiGet(
        download_runc_tool,
        image,
        Get(Digest, CreateDigest([Directory("runspace")])),
        Get(JqBinary, JqBinaryRequest()),
    )

    if image.exit_code != 0:
        raise ValueError(image.stderr)

    packed_image_process = await Get(Process, UnpackedImageBundleRequest(image.output.digest))

    name = str(request.target.address).replace("/", "_").replace(":", "_").replace("#", "_")
    components = [
        dedent(
            f"""
        ROOT=`pwd`
        for arg in "$@"; do
            echo "$arg" | {jq.path} -R --argjson config "$(cat $ROOT/unpacked_image/config.json)" '
                    .  as $arg  | $config | .process.args += [$arg]
                ' > "$ROOT/unpacked_image/config.json"
        done"""
        )
    ]

    if not request.target.get(ImageRunTty).value:
        components.append(
            dedent(
                f"""
                {jq.path} '
                    .process.terminal = false
                ' "$ROOT/unpacked_image/config.json" > "$ROOT/unpacked_image/config.json.tmp"
                {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"
                """
            )
        )

    components.append(
        dedent(
            f"""
            `pwd`/{tool.exe} --root runspace run -b unpacked_image pants.runc.{name}
            exit
            """
        )
    )
    script_digest = await Get(
        Digest, CreateDigest([FileContent("run.sh", "\n".join(components).encode("utf-8"))])
    )
    input_digest = await Get(Digest, MergeDigests((rundir, tool.digest, script_digest)))

    return await Get(
        Process,
        FusedProcess(
            (
                packed_image_process,
                Process(
                    ("sh", "{chroot}/run.sh", "$*"),
                    description=f"Running {request.target}",
                    input_digest=input_digest,
                ),
            )
        ),
    )


@rule
async def run_oci_command_target(request: RunImageBundleCommand) -> RunRequest:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.address, description_of_origin="package_oci_image"),
    )
    process = await Get(Process, RunImageBundleProcessRequest(wrapped_target.target))

    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
    )


@rule
async def run_debug_oci_command_target(
    field_set: RunImageBundleCommand,
) -> RunDebugAdapterRequest:
    raise NotImplementedError("Cannot run kubernetes commands in debug mode.")


def rules():
    return [*collect_rules(), UnionRule(RunFieldSet, RunImageBundleCommand)]
