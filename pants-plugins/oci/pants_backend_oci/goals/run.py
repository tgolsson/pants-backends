"""

"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from pants.core.goals.run import RunDebugAdapterRequest, RunFieldSet, RunRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import (
    SEARCH_PATHS,
    BinaryShims,
    BinaryShimsRequest,
    MkdirBinary,
    MvBinary,
)
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import Target, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.version import PANTS_SEMVER, Version

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

    # TODO[TSolberg]: This should be handled in the utility rule later.
    tools = ["newuidmap", "newgidmap", "jq", "cat", "echo", "sh"]
    kwargs = dict(
        rationale="runc",
        search_path=SEARCH_PATHS,
    )
    if PANTS_SEMVER < Version("2.16.0.dev0"):
        kwargs["output_directory"] = "bins"

    binary_shims = BinaryShimsRequest.for_binaries(
        *tools,
        **kwargs,
    )

    tool, image, rundir, shims = await MultiGet(
        download_runc_tool,
        image,
        Get(Digest, CreateDigest([Directory("runspace")])),
        Get(
            BinaryShims,
            BinaryShimsRequest,
            binary_shims,
        ),
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
            echo "$arg" | jq -R --argjson config "$(cat $ROOT/unpacked_image/config.json)" '
                    .  as $arg  | $config | .process.args += [$arg]
                ' > "$ROOT/unpacked_image/config.json"
        done
        cat $ROOT/unpacked_image/config.json | jq '
                    .process.terminal = false |
                    [
                        "CAP_AUDIT_WRITE",
                        "CAP_CHOWN",
                        "CAP_DAC_OVERRIDE",
                        "CAP_FOWNER",
                        "CAP_FSETID",
                        "CAP_KILL",
                        "CAP_MKNOD",
                        "CAP_NET_BIND_SERVICE",
                        "CAP_NET_RAW",
                        "CAP_SETFCAP",
                        "CAP_SETGID",
                        "CAP_SETPCAP",
                        "CAP_SETUID",
                        "CAP_SYS_CHROOT"
                    ] as $caps |
                    .process.capabilities.effective = $caps |
                    .process.capabilities.inheritable = $caps |
                    .process.capabilities.permitted = $caps |
                    .process.capabilities.bounding = $caps |
                    .process.capabilities.ambient = $caps
                ' > "$ROOT/unpacked_image/config.json.tmp"
            {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"
            """
        )
    ]

    if not request.target.get(ImageRunTty).value:
        components.append(
            dedent(
                f"""
                jq '
                    .process.terminal = false
                ' "$ROOT/unpacked_image/config.json" > "$ROOT/unpacked_image/config.json.tmp"
                {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"
                """
            )
        )

    components.append(
        dedent(
            f"""
            `pwd`/{tool.exe} --root runspace --rootless true run -b unpacked_image pants.runc.{name}
            exit
            """
        )
    )
    script_digest = await Get(
        Digest, CreateDigest([FileContent("run.sh", "\n".join(components).encode("utf-8"))])
    )

    if PANTS_SEMVER >= Version("2.16.0.dev0"):
        immutable_input_digests = shims.immutable_input_digests
        env = {"PATH": shims.path_component, "XDG_RUNTIME_DIR": "{chroot}/tmp"}
        input_digest = await Get(Digest, MergeDigests((rundir, tool.digest, script_digest)))

    else:
        env = {"PATH": "{chroot}/bins", "XDG_RUNTIME_DIR": "{chroot}/tmp"}
        immutable_input_digests = None
        input_digest = await Get(Digest, MergeDigests((rundir, tool.digest, script_digest, shims.digest)))

    return await Get(
        Process,
        FusedProcess(
            (
                packed_image_process,
                Process(
                    ("/usr/bin/sh", "{chroot}/run.sh", "$*"),
                    description=f"Running {request.target}",
                    input_digest=input_digest,
                    immutable_input_digests=immutable_input_digests,
                    env=env,
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
        immutable_input_digests=process.immutable_input_digests,
    )


@rule
async def run_debug_oci_command_target(
    field_set: RunImageBundleCommand,
) -> RunDebugAdapterRequest:
    raise NotImplementedError("Cannot run kubernetes commands in debug mode.")


def rules():
    return [*collect_rules(), UnionRule(RunFieldSet, RunImageBundleCommand)]
