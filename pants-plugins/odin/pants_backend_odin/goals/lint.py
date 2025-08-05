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
from pants.engine.target import FieldSet, Target, Targets, DependenciesRequest
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinDependenciesField, OdinSourceField


@dataclass(frozen=True)
class OdinPackageFieldSet(FieldSet):
    required_fields = (OdinDependenciesField,)

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

        if source_path not in directories:
            directories[source_path] = []

        directories[source_path].append(field_set)

    return Partitions(Partition(frozenset(v), metadata=BatchMetadata(d)) for d, v in directories.items())


@rule(level=LogLevel.DEBUG, desc="Lint Odin packages with Odin check")
async def odin_package_lint(
    request: OdinPackageLintRequest.Batch[OdinPackageFieldSet, BatchMetadata], odin: OdinTool, platform: Platform
) -> LintResult:
    download_odin_get = Get(DownloadedExternalTool, ExternalToolRequest, odin.get_request(platform))

    # Get the dependencies of the odin_package targets to find the source files
    dependencies_gets = [
        Get(Targets, DependenciesRequest, DependenciesRequest(field_set.dependencies))
        for field_set in request.elements
    ]
    
    downloaded_odin, *all_dependencies = await MultiGet(download_odin_get, *dependencies_gets)
    
    # Collect all source files from the dependencies
    source_field_sets = []
    for dependencies in all_dependencies:
        for target in dependencies:
            if target.has_field(OdinSourceField):
                source_field_sets.append(target[OdinSourceField])
    
    if not source_field_sets:
        # No source files found, nothing to lint
        return LintResult.create(request, FallibleProcessResult((), 0, b"", b""))

    # Get the source files
    sources_digest = await Get(
        SourceFiles, SourceFilesRequest(source_field_sets)
    )
    
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
        *OdinPackageLintRequest.rules(),
    ]
