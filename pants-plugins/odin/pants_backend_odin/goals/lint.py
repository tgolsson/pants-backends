from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import Partition, Partitions
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import (
    OdinDependenciesField,
    _OdinPackageMarkerField,
)
from pants_backend_odin.util_rules.sandbox import PrepareOdinSandboxRequest, PrepareOdinSandboxResult


@dataclass(frozen=True)
class OdinPackageFieldSet(FieldSet):
    required_fields = (OdinDependenciesField, _OdinPackageMarkerField)

    dependencies: OdinDependenciesField


class OdinPackageLintRequest(LintTargetsRequest):
    field_set_type = OdinPackageFieldSet
    tool_subsystem = OdinTool


@dataclass(frozen=True)
class BatchMetadata:
    directory: str

    @property
    def description(self) -> None:
        return None


@rule(level=LogLevel.DEBUG, desc="Partition Odin package targets for linting")
async def partition_odin_package_sources(
    request: OdinPackageLintRequest.PartitionRequest[OdinPackageFieldSet], odin: OdinTool
) -> Partitions[OdinPackageFieldSet, BatchMetadata]:
    if odin.skip:
        return Partitions()

    directories = {}

    for field_set in request.field_sets:
        source_path = field_set.address.spec_path

        assert source_path not in directories, "can only have one `odin_package` per directory."
        directories[source_path] = field_set

    return Partitions(Partition([v], metadata=BatchMetadata(d)) for d, v in directories.items())


@rule(level=LogLevel.DEBUG, desc="Lint Odin packages with Odin check")
async def odin_package_lint(
    request: OdinPackageLintRequest.Batch[OdinPackageFieldSet, BatchMetadata],
    odin: OdinTool,
    platform: Platform,
) -> LintResult:
    download_odin_get = Get(DownloadedExternalTool, ExternalToolRequest, odin.get_request(platform))

    # Prepare sandboxes for all field sets
    sandbox_gets = [
        Get(PrepareOdinSandboxResult, PrepareOdinSandboxRequest(field_set.address))
        for field_set in request.elements
    ]

    downloaded_odin, *all_sandbox_results = await MultiGet(download_odin_get, *sandbox_gets)

    # Check if we have any source files across all sandboxes
    total_source_files = []
    for sandbox_result in all_sandbox_results:
        total_source_files.extend(sandbox_result.source_files)

    if not total_source_files:
        # No source files found, nothing to lint
        return LintResult.create(request, FallibleProcessResult((), 0, b"", b""))

    # Merge all sandbox digests and the Odin tool digest
    all_digests = [downloaded_odin.digest]
    all_digests.extend(sandbox_result.digest for sandbox_result in all_sandbox_results)
    
    input_digest = await Get(Digest, MergeDigests(all_digests))

    process_result = await Get(
        FallibleProcessResult,
        Process(
            argv=[
                downloaded_odin.exe,
                "check",
                request.partition_metadata.directory,
                "-no-entry-point",
            ],
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
        *OdinPackageLintRequest.rules(),
    ]
