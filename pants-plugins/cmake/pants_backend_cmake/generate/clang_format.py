"""The cc backend's clang-format `fmt` process, so generated files come out as `pants fmt` would
leave them. `[clang-format].skip` is not read: it opts out of `fmt`/`lint`, while here it would
change the bytes of generated files."""

from __future__ import annotations

from pants.backend.cc.lint.clangformat import subsystem as clangformat_subsystem
from pants.backend.cc.lint.clangformat.subsystem import ClangFormat
from pants.backend.python.util_rules.pex import PexProcess, create_pex
from pants.core.util_rules.config_files import find_config_file
from pants.engine.fs import MergeDigests
from pants.engine.intrinsics import digest_to_snapshot, merge_digests
from pants.engine.process import fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pants_backend_generate.util_rules.formatting import FormatOutputsRequest, FormattedOutputs


class ClangFormatOutputsRequest(FormatOutputsRequest):
    pass


@rule(desc="Format generated C/C++ sources with clang-format", level=LogLevel.DEBUG)
async def clang_format_outputs(
    request: ClangFormatOutputsRequest, clangformat: ClangFormat
) -> FormattedOutputs:
    clangformat_pex, config_files = await concurrently(
        create_pex(clangformat.to_pex_request()),
        find_config_file(clangformat.config_request(request.snapshot.dirs)),
    )
    input_digest = await merge_digests(
        MergeDigests([request.snapshot.digest, config_files.snapshot.digest, clangformat_pex.digest])
    )
    files = request.snapshot.files
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            PexProcess(
                clangformat_pex,
                argv=("--style=file", "--fallback-style=webkit", "-i", "--Werror", *clangformat.args, *files),
                input_digest=input_digest,
                output_files=files,
                description=f"Run clang-format on {pluralize(len(files), 'file')} of {request.address}.",
                level=LogLevel.DEBUG,
            )
        ),
    )
    return FormattedOutputs(await digest_to_snapshot(result.output_digest))


def rules():
    return [
        *collect_rules(),
        *clangformat_subsystem.rules(),
        UnionRule(FormatOutputsRequest, ClangFormatOutputsRequest),
    ]
