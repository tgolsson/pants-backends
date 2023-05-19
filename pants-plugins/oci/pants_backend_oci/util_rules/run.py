from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import (
    SEARCH_PATHS,
    BashBinary,
    BinaryShims,
    BinaryShimsRequest,
    CatBinary,
    CpBinary,
    MkdirBinary,
    MvBinary,
)
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.version import PANTS_SEMVER, Version

from pants_backend_oci.subsystem import OciSubsystem, RuncTool, UmociTool
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
    oci: OciSubsystem,
    platform: Platform,
    bash: BashBinary,
    cat: CatBinary,
    cp: CpBinary,
    mkdir: MkdirBinary,
    mv: MvBinary,
) -> ProcessResult:
    tools = ["newuidmap", "newgidmap", "jq", "cp", "ls"]
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
    tool, rundir, jq, shims, packed_image_process = await MultiGet(
        Get(DownloadedExternalTool, ExternalToolRequest, runc.get_request(platform)),
        Get(Digest, CreateDigest([Directory("runspace")])),
        Get(JqBinary, JqBinaryRequest()),
        Get(
            BinaryShims,
            BinaryShimsRequest,
            binary_shims,
        ),
        Get(Process, UnpackedImageBundleRequest(request.bundle.digest)),
    )

    shell_command = [f'"{c}"' for c in oci.command_shell]

    uid_mappings = ",\n".join(
        [
            f"""
        {{
            "containerID": {container_id},
            "hostID": {host_id},
            "size": {size}
        }}
        """
            for container_id, host_id, size in map(lambda desc: desc.split(":"), oci.uid_map)
        ]
    )

    gid_mappings = ",\n".join(
        [
            f"""
        {{
            "containerID": {container_id},
            "hostID": {host_id},
            "size": {size}
        }}
        """
            for container_id, host_id, size in map(lambda desc: desc.split(":"), oci.gid_map)
        ]
    )

    script = dedent(
        f"""
        ROOT=`pwd`
        set -euxo
        cp $ROOT/unpacked_image/config.json $ROOT/unpacked_image/config.json.bak
        {cat.path} $ROOT/unpacked_image/config.json | {jq.path} '
                    .process.args = [{", ".join(shell_command)}, "{request.command}"]
                ' > "$ROOT/unpacked_image/config.json.tmp"
        {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"

        {cat.path} $ROOT/unpacked_image/config.json | {jq.path} '
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

            {cat.path} $ROOT/unpacked_image/config.json | {jq.path} '
                    .linux.uidMappings = [{uid_mappings}] |
                    .linux.gidMappings = [{gid_mappings}] |
                    .mounts[0].options = [
                             "nosuid",
                             "noexec",
                             "nodev"
                       ]
                       | .process.user.uid = 0
                       | .process.user.gid = 0
                       | .mounts += [ {{
                              "destination": "/sys",
                              "type": "bind",
                              "source": "/sys",
                              "options": [
                                "rprivate",
                                "nosuid",
                                "noexec",
                                "nodev",
                                "ro",
                                "rbind"
                              ]
                            }}
                       ]
            ' > "$ROOT/unpacked_image/config.json.tmp"
        {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"
        `pwd`/{tool.exe} --debug --root runspace --rootless true run -b unpacked_image pants.runc
        cp $ROOT/unpacked_image/config.json.bak $ROOT/unpacked_image/config.json
    """
    )
    script_digest = await Get(Digest, CreateDigest([FileContent("run.sh", script.encode("utf-8"))]))

    if PANTS_SEMVER >= Version("2.16.0.dev0"):
        immutable_input_digests = shims.immutable_input_digests
        env = {"PATH": shims.path_component, "XDG_RUNTIME_DIR": "{chroot}/tmp"}
        input_digest = await Get(Digest, MergeDigests((rundir, tool.digest, script_digest)))

    else:
        env = {"PATH": "{chroot}/bins", "XDG_RUNTIME_DIR": "{chroot}/tmp"}
        immutable_input_digests = None
        input_digest = await Get(Digest, MergeDigests((rundir, tool.digest, script_digest, shims.digest)))

    steps = [
        packed_image_process,
        Process(
            (bash.path, "{chroot}/run.sh", f"{request.command}"),
            description=f"Running container build command {request.command}",
            input_digest=input_digest,
            immutable_input_digests=immutable_input_digests,
            env=env,
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
