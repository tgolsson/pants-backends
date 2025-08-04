from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import Partition, Partitions
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.util.logging import LogLevel
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


@dataclass(frozen=True)
class BatchMetadata:
    directory: str

    @property
    def description(self) -> None:
        return None


@rule(level=LogLevel.DEBUG, desc="Partition Odin source files for linting")
async def partition_odin_sources(
    request: OdinLintRequest.PartitionRequest[OdinFieldSet], odin: OdinTool
) -> Partitions[OdinFieldSet, BatchMetadata]:
    if odin.skip:
        return Partitions()

    directories = {}
    for field_set in request.field_sets:
        source_path = field_set.address.spec_path

        if source_path not in directories:
            directories[source_path] = []

        directories[source_path].append(field_set)

    return Partitions(Partition(frozenset(v), metadata=BatchMetadata(d)) for d, v in directories.items())


@rule(level=LogLevel.DEBUG, desc="Lint with Odin check")
async def odin_lint(
    request: OdinLintRequest.Batch[OdinFieldSet, BatchMetadata], odin: OdinTool, platform: Platform
) -> LintResult:
    download_odin_get = Get(DownloadedExternalTool, ExternalToolRequest, odin.get_request(platform))

    # Get the source files
    sources_digest_get = Get(
        SourceFiles, SourceFilesRequest([field_set.source for field_set in request.elements])
    )

    downloaded_odin, sources_digest = await MultiGet(download_odin_get, sources_digest_get)
    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                downloaded_odin.digest,
                sources_digest.snapshot.digest,
            ]
        ),
    )

    process_result = await Get(
        FallibleProcessResult,
        Process(
            argv=[downloaded_odin.exe, "check", request.partition_metadata.directory],
            input_digest=input_digest,
            description=f"Run odin check on {request.partition_metadata.directory}",
        ),
    )

    return LintResult.create(
        request,
        process_result,
    )


def rules():
    return [
        *collect_rules(),
        *OdinLintRequest.rules(),
    ]
