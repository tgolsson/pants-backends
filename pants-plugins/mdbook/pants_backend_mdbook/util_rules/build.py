"""

"""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.fs import Digest
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule

from pants_backend_mdbook.subsystem import MdBookTool
from pants_backend_mdbook.util_rules.prepare import MdBookAnalysis, MdBookAnalysisRequest


@dataclass(frozen=True)
class MdBookBuildOutput:
    digest: Digest | None


@dataclass(frozen=True)
class FallibleMdBookBuildOutput:
    success: bool

    output: MdBookBuildOutput | None = None


@dataclass(frozen=True)
class MdbookBuildRequest:
    address: Address


@rule(desc="Building MDBook")
async def build_mdbook(
    request: MdbookBuildRequest,
    mdbook: MdBookTool,
) -> FallibleMdBookBuildOutput:
    analysis = await Get(MdBookAnalysis, MdBookAnalysisRequest(request.address))
    result = await Get(
        ProcessResult,
        Process(
            input_digest=analysis.digest,
            argv=(analysis.tool_exe, "build", analysis.build_root),
            description=f"Building mdbook: {request.address}",
            output_directories=(f"{analysis.build_root}/book",),
        ),
    )

    return FallibleMdBookBuildOutput(
        success=True,
        output=MdBookBuildOutput(result.output_digest),
    )


def rules():
    return collect_rules()
