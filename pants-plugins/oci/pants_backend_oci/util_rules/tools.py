from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BinaryShims,
    BinaryShimsRequest,
    SystemBinariesSubsystem,
)
from pants.engine.rules import Get, collect_rules, rule

_TOOLS = [
    "newuidmap",
    "newgidmap",
    "jq",
    "cat",
    "echo",
    "sh",
    "cp",
    "ls",
]


@dataclass(frozen=True)
class RuncToolsRequest:
    pass


@rule
async def get_binary_shims(
    _: RuncToolsRequest, system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware
) -> BinaryShims:
    kwargs = dict(
        rationale="runc",
        search_path=system_binaries_subsystem.system_binary_paths,
    )

    binary_shims = BinaryShimsRequest.for_binaries(
        *_TOOLS,
        **kwargs,
    )

    return await Get(BinaryShims, BinaryShimsRequest, binary_shims)


def rules():
    return [
        *collect_rules(),
    ]
