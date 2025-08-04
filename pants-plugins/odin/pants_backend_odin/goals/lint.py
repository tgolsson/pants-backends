from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import Partitions
from pants.engine.fs import Digest, GlobMatchErrorBehavior, PathGlobs
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinSourceField


@dataclass(frozen=True)
class OdinFieldSet(FieldSet):
    required_fields = (OdinSourceField,)

    source: OdinSourceField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return tgt.get(OdinSourceField).value is None


class OdinLintRequest(LintTargetsRequest):
    field_set_type = OdinFieldSet
    tool_subsystem = OdinTool


@rule(level=LogLevel.DEBUG, desc="Partition Odin source files for linting")
async def partition_odin_sources(
    request: OdinLintRequest.PartitionRequest[OdinFieldSet], odin: OdinTool
) -> Partitions[OdinFieldSet, None]:
    if odin.skip:
        return Partitions()
    return Partitions.single_partition(request.field_sets)


@rule(level=LogLevel.DEBUG, desc="Lint with Odin check")
async def odin_lint(request: OdinLintRequest.Batch, odin: OdinTool) -> LintResult:
    download_odin_get = Get(DownloadedExternalTool, ExternalToolRequest, odin.get_request())
    
    # Get the source files
    sources_paths = [field_set.source.file_path for field_set in request.elements]
    sources_digest_get = Get(
        Digest,
        PathGlobs(
            sources_paths,
            glob_match_error_behavior=GlobMatchErrorBehavior.error,
        ),
    )

    downloaded_odin, sources_digest = await MultiGet(download_odin_get, sources_digest_get)

    # Create a process to run odin check on each source file
    processes = []
    for source_path in sources_paths:
        process = Process(
            argv=[downloaded_odin.exe, "check", source_path],
            input_digest=sources_digest,
            description=f"Run odin check on {source_path}",
        )
        processes.append(Get(FallibleProcessResult, Process, process))

    process_results = await MultiGet(*processes)

    # Combine results
    exit_code = 0
    stdout = ""
    stderr = ""
    
    for i, result in enumerate(process_results):
        if result.exit_code != 0:
            exit_code = result.exit_code
        stdout += result.stdout.decode()
        stderr += result.stderr.decode()

    return LintResult.create(
        request,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        report=None,
    )


def rules():
    return [
        *collect_rules(),
        *OdinLintRequest.rules(),
    ]