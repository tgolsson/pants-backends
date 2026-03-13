""" """

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.fs import Digest
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule

from pants_backend_mdbook.subsystem import MdBookTool
from pants_backend_mdbook.util_rules.prepare import MdBookAnalysisRequest, prepare_md_book_ctx


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
    analysis = await prepare_md_book_ctx(MdBookAnalysisRequest(request.address), **implicitly())
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                input_digest=analysis.digest,
                argv=(analysis.tool_exe, "build", analysis.build_root),
                description=f"Building mdbook: {request.address}",
                output_directories=(f"{analysis.build_root}/book",),
            ),
        ),
    )

    return FallibleMdBookBuildOutput(
        success=True,
        output=MdBookBuildOutput(result.output_digest),
    )


def rules():
    return collect_rules()
