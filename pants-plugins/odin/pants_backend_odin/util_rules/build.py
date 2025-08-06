from __future__ import annotations

from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import (
    BinaryShims,
    BinaryShimsRequest,
    SystemBinariesSubsystem,
)
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinTool


@dataclass(frozen=True)
class BuildOdinfmtRequest:
    """Request to build odinfmt from OLS repository."""

    platform: Platform


@dataclass(frozen=True)
class BuildOdinfmtResult:
    """Result of building odinfmt."""

    digest: Digest
    exe_path: str


@rule(level=LogLevel.DEBUG, desc="Build odinfmt from OLS repository")
async def build_odinfmt(
    request: BuildOdinfmtRequest,
    odin: OdinTool,
    system_binaries_environment: SystemBinariesSubsystem.EnvironmentAware,
) -> BuildOdinfmtResult:
    """Build odinfmt from the OLS repository."""

    # Get required system binaries
    binary_shims = await Get(
        BinaryShims,
        BinaryShimsRequest.for_binaries(
            "git",
            "bash",
            "chmod",
            "cp",
            "mv",
            "date",
            "dirname",
            "realpath",
            "clang",
            rationale="Building odinfmt from OLS",
            search_path=system_binaries_environment.system_binary_paths,
        ),
    )

    # Get the Odin compiler
    downloaded_odin = await Get(
        DownloadedExternalTool, ExternalToolRequest, odin.get_request(request.platform)
    )

    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                downloaded_odin.digest,
                binary_shims.digest,
            ]
        ),
    )

    # Clone the OLS repository and build odinfmt
    process_result = await Get(
        ProcessResult,
        Process(
            argv=[
                "bash",
                "-c",
                f"""
                set -e
                DIR=$(pwd)
                export PATH="$DIR/$(dirname "{downloaded_odin.exe}"):$PATH"
                git clone https://github.com/DanielGavin/ols.git
                pushd ols
                ./odinfmt.sh
                cp odinfmt ..
                popd
                """,
            ],
            input_digest=input_digest,
            description="Clone OLS and build odinfmt",
            output_files=("./odinfmt",),
            env={"PATH": f"{binary_shims.path_component}"},
            immutable_input_digests={
                **binary_shims.immutable_input_digests,
            },
        ),
    )

    return BuildOdinfmtResult(
        digest=process_result.output_digest,
        exe_path="./odinfmt",
    )


def rules():
    return [
        *collect_rules(),
    ]
