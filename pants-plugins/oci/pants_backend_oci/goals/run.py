""" """

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from pants.core.goals.repl import ReplImplementation, ReplRequest
from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior, RunRequest
from pants.core.util_rules.external_tool import download_external_tool
from pants.core.util_rules.system_binaries import MkdirBinary, MvBinary
from pants.engine.fs import CreateDigest, Directory, FileContent, MergeDigests
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, concurrently, implicitly, rule
from pants.engine.target import Target, WrappedTargetRequest
from pants.engine.unions import UnionRule

from pants_backend_oci.subsystem import OciSubsystem, RuncTool
from pants_backend_oci.target_types import ImageRepository, ImageRunTty
from pants_backend_oci.tools.process import FusedProcess, fuse_process
from pants_backend_oci.util_rules.configure import SetCmdProcessRequest, set_args
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
)
from pants_backend_oci.util_rules.tools import RuncToolsRequest, get_binary_shims
from pants_backend_oci.util_rules.unpack import UnpackedImageBundleRequest, make_unpack_process


@dataclass(frozen=True)
class RunImageBundleCommand(RunFieldSet):
    required_fields = (ImageRepository,)
    run_in_sandbox_behavior = RunInSandboxBehavior.RUN_REQUEST_HERMETIC

    repository: ImageRepository
    run_tty: ImageRunTty


@dataclass(frozen=True)
class RunImageBundleProcessRequest:
    target: Target

    interactive: bool = False


@rule
async def prepare_run_image_bundle(
    request: RunImageBundleProcessRequest,
    tool: RuncTool,
    oci: OciSubsystem,
    mkdir_binary: MkdirBinary,
    mv: MvBinary,
    platform: Platform,
) -> Process:
    download_runc_tool = download_external_tool(tool.get_request(platform))
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.target.address, description_of_origin="package_oci_image"),
        **implicitly(),
    )
    target = wrapped_target.target
    bundle_request = await ibr_to_fibr(ImageBundleRequest(target), **implicitly())
    image = Get(FallibleImageBundle, FallibleImageBundleRequest, bundle_request.request)

    tool, image, rundir, shims = await concurrently(
        download_runc_tool,
        image,
        create_digest(CreateDigest([Directory("runspace")])),
        get_binary_shims(RuncToolsRequest(), **implicitly()),
    )

    if image.exit_code != 0:
        raise ValueError(image.stderr)

    packed_image_process, set_cmd_process = await concurrently(
        make_unpack_process(UnpackedImageBundleRequest(image.output.digest), **implicitly()),
        set_args(SetCmdProcessRequest(), **implicitly()),
    )

    name = str(request.target.address).replace("/", "_").replace(":", "_").replace("#", "_")
    components = [
        dedent(f"""
        ROOT=`pwd`
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
            """)
    ]

    terminal = request.target.get(ImageRunTty).value
    if request.interactive:
        terminal = True

    components.append(
        dedent(f"""
                jq '
                    .process.terminal = {"true" if terminal else "false"}
                ' "$ROOT/unpacked_image/config.json" > "$ROOT/unpacked_image/config.json.tmp"
                {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"
                """)
    )

    rootless = "true" if oci.rootless else "false"
    suffix = "" if request.interactive else " 0<&-"
    container = f"pants.runc.{name}"
    components.append(
        dedent(f"""
            `pwd`/{tool.exe} --root runspace --rootless {rootless} run -b unpacked_image {container}{suffix}
            """)
    )
    script_digest = await create_digest(
        CreateDigest([FileContent("run.sh", "\n".join(components).encode("utf-8"))])
    )

    immutable_input_digests = shims.immutable_input_digests
    env = {"PATH": shims.path_component, "XDG_RUNTIME_DIR": "{chroot}/tmp"}
    input_digest = await merge_digests(MergeDigests((rundir, tool.digest, script_digest)))

    return await fuse_process(
        FusedProcess(
            (
                set_cmd_process,
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
        **implicitly(),
    )


@rule
async def run_oci_command_target(request: RunImageBundleCommand) -> RunRequest:
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="package_oci_image"), **implicitly()
    )
    process = await prepare_run_image_bundle(
        RunImageBundleProcessRequest(wrapped_target.target), **implicitly()
    )

    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
        immutable_input_digests=process.immutable_input_digests,
    )


class OciRepl(ReplImplementation):
    name = "oci"


@rule
async def run_oci_command_repl(request: OciRepl) -> ReplRequest:
    process = await prepare_run_image_bundle(
        RunImageBundleProcessRequest(request.targets[0], interactive=True), **implicitly()
    )
    return ReplRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
        immutable_input_digests=process.immutable_input_digests,
    )


def rules():
    rules = [
        *collect_rules(),
        *RunImageBundleCommand.rules(),
        UnionRule(ReplImplementation, OciRepl),
    ]

    return rules
