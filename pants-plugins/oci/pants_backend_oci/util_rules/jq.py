from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BinaryPath,
    BinaryPathRequest,
    BinaryPaths,
    BinaryPathTest,
    SystemBinariesSubsystem,
)
from pants.engine.rules import Get, collect_rules, rule


class JqBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class JqBinaryRequest:
    pass


@rule
async def find_jq_wrapper(_: JqBinaryRequest, jq_binary: JqBinary) -> JqBinary:
    return jq_binary


@rule(desc="Finding the `jq` binary")
async def find_jq(
    system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
) -> JqBinary:
    request = BinaryPathRequest(
        binary_name="jq",
        search_path=system_binaries_subsystem.system_binary_paths,
        test=BinaryPathTest(args=["--version"]),
    )
    paths = await Get(BinaryPaths, BinaryPathRequest, request)
    first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
    return JqBinary(first_path.path, first_path.fingerprint)


def rules():
    return [
        *collect_rules(),
    ]
