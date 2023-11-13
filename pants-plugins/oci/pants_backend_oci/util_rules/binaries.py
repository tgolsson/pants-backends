from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BinaryPath,
    BinaryPathRequest,
    BinaryPaths,
    BinaryPathTest,
)
from pants.engine.rules import Get, collect_rules, rule
from pants.version import PANTS_SEMVER, Version


class NewUidMapBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class NewUidMapBinaryRequest:
    pass


@rule
async def find_newuidmap_wrapper(
    _: NewUidMapBinaryRequest, newuidmap_binary: NewUidMapBinary
) -> NewUidMapBinary:
    return newuidmap_binary


if PANTS_SEMVER >= Version("2.19.0.dev0"):
    from pants.core.util_rules.system_binaries import SystemBinariesSubsystem

    @rule(desc="Finding the `newuidmap` binary")
    async def find_newuidmap(
        system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
    ) -> NewUidMapBinary:
        request = BinaryPathRequest(
            binary_name="newuidmap",
            search_path=system_binaries_subsystem,
            test=BinaryPathTest(args=["--version"]),
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return NewUidMapBinary(first_path.path, first_path.fingerprint)

else:
    from pants.core.util_rules.system_binaries import SEARCH_PATHS

    @rule(desc="Finding the `newuidmap` binary")
    async def find_newuidmap() -> NewUidMapBinary:
        request = BinaryPathRequest(
            binary_name="newuidmap", search_path=SEARCH_PATHS, test=BinaryPathTest(args=["--version"])
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return NewUidMapBinary(first_path.path, first_path.fingerprint)


def rules():
    return [
        *collect_rules(),
    ]
