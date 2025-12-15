from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from pants.core.util_rules.external_tool import download_external_tool
from pants.core.util_rules.system_binaries import (
    BashBinary,
    CatBinary,
    CpBinary,
    MkdirBinary,
    MvBinary,
)
from pants.engine.fs import CreateDigest, Directory, FileContent, MergeDigests
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule

from pants_backend_oci.subsystem import OciSubsystem, RuncTool, UmociTool
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.jq import JqBinaryRequest, find_jq_wrapper
from pants_backend_oci.util_rules.tools import RuncToolsRequest, get_binary_shims
from pants_backend_oci.util_rules.unpack import (
    RepackedImageBundleRequest,
    UnpackedImageBundleRequest,
    make_repack_process,
    make_unpack_process,
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
    tool, rundir, jq, shims, packed_image_process = await concurrently(
        download_external_tool(runc.get_request(platform)),
        create_digest(CreateDigest([Directory("runspace")])),
        find_jq_wrapper(JqBinaryRequest(), **implicitly()),
        get_binary_shims(RuncToolsRequest(), **implicitly()),
        make_unpack_process(UnpackedImageBundleRequest(request.bundle.digest), **implicitly()),
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

    command = request.command.replace('"', '\\"')

    if not oci.rootless:
        rootness_patches = '| del(.linux.namespaces[] | select(.type == "network" or .type == "user"))'

    else:
        rootness_patches = """| .mounts += [ {
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
                            }
                       ]"""

    rootless = "true" if oci.rootless else "false"
    namespace = f"pants.runc.{request.bundle.digest.fingerprint}"

    script = dedent(f"""
        ROOT=`pwd`

        cp $ROOT/unpacked_image/config.json $ROOT/unpacked_image/config.json.bak
        {cat.path} $ROOT/unpacked_image/config.json | {jq.path} '
                    .process.args = [{", ".join(shell_command)}, "{command}" ]
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
                       .mounts += [ {{
                           "destination": "/etc/resolv.conf",
                            "type": "bind",
                            "source": "/etc/resolv.conf",
                            "options": [
                              "ro",
                              "rbind",
                              "rprivate",
                              "nosuid",
                              "noexec",
                              "nodev"
                            ]
                          }},
                          {{
                            "destination": "/run",
                            "type": "tmpfs",
                            "source": "tmpfs",
                            "options": [
                              "noexec",
                              "nosuid",
                              "nodev",
                              "rprivate"
                            ]
                          }}
                       ]
                       | .mounts[0].options = [ "nosuid", "noexec", "nodev" ]
                       | .process.user.uid = 0
                       | .process.user.gid = 0
                       {rootness_patches}
            ' > "$ROOT/unpacked_image/config.json.tmp"
        {mv.path} "$ROOT/unpacked_image/config.json.tmp" "$ROOT/unpacked_image/config.json"
        `pwd`/{tool.exe} --debug --root runspace --rootless {rootless} run -b unpacked_image {namespace} 0<&-
        cp $ROOT/unpacked_image/config.json.bak $ROOT/unpacked_image/config.json
    """)
    script_digest = await create_digest(CreateDigest([FileContent("run.sh", script.encode("utf-8"))]))

    immutable_input_digests = shims.immutable_input_digests
    env = {"PATH": shims.path_component, "XDG_RUNTIME_DIR": "{chroot}/tmp"}
    input_digest = await merge_digests(MergeDigests((rundir, tool.digest, script_digest)))

    steps = [
        packed_image_process,
        Process(
            (
                bash.path,
                "{chroot}/run.sh",
            ),
            description="Running container build command",
            input_digest=input_digest,
            immutable_input_digests=immutable_input_digests,
            env=env,
        ),
    ]

    if request.repack:
        steps.append(await make_repack_process(RepackedImageBundleRequest(request.command), **implicitly()))

    res = await fallible_to_exec_result_or_raise(**implicitly(FusedProcess(tuple(steps))))
    return res


def rules():
    return collect_rules()
