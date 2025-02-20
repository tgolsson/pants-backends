from __future__ import annotations

from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, rule

from pants_backend_oci.subsystem import OciSubsystem, UmociTool


@dataclass(frozen=True)
class SetCmdProcessRequest:
    pass


@rule
async def set_args(
    request: SetCmdProcessRequest, tool: UmociTool, platform: Platform, oci: OciSubsystem
) -> Process:
    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

    command = [
        rf"""
        # nosplit
        if [ $# -gt 0 ]; then
            {{chroot}}/{umoci.exe} --log={tool.log} config --clear config.cmd --image build:build
            i=0
            args=()
            for arg in "$@"; do
                args[$i]="--config.cmd=$arg"
                ((++i))
            done
            {{chroot}}/{umoci.exe} --log={tool.log} config "${{args[@]}}" --image build:build
        fi
        """
    ]

    return Process(
        tuple(command),
        description="Setting arguments",
    )


def rules():
    return collect_rules()
