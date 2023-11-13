from dataclasses import dataclass

from pants.core.util_rules.system_binaries import BinaryShims, BinaryShimsRequest
from pants.engine.rules import Get, collect_rules, rule
from pants.version import PANTS_SEMVER, Version

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


if PANTS_SEMVER >= Version("2.19.0.dev0"):
    from pants.core.util_rules.system_binaries import SystemBinariesSubsystem

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

else:
    from pants.core.util_rules.system_binaries import SEARCH_PATHS

    @rule
    async def get_binary_shims(_: RuncToolsRequest) -> BinaryShims:
        kwargs = dict(
            rationale="runc",
            search_path=SEARCH_PATHS,
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
