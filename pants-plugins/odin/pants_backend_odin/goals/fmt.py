from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.partitions import Partition, Partitions
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.target import DependenciesRequest, FieldSet, Targets
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinfmtTool
from pants_backend_odin.target_types import OdinDependenciesField, OdinSourceField
from pants_backend_odin.util_rules.build import BuildOdinfmtRequest, BuildOdinfmtResult


@dataclass(frozen=True)
class OdinPackageFieldSet(FieldSet):
    required_fields = (OdinDependenciesField,)

    dependencies: OdinDependenciesField


class OdinPackageFmtRequest(FmtTargetsRequest):
    field_set_type = OdinPackageFieldSet
    tool_subsystem = OdinfmtTool


@dataclass(frozen=True)
class BatchMetadata:
    directory: str

    @property
    def description(self) -> None:
        return None


@rule(level=LogLevel.DEBUG, desc="Partition Odin package targets for formatting")
async def partition_odin_package_sources(
    request: OdinPackageFmtRequest.PartitionRequest[OdinPackageFieldSet], odinfmt: OdinfmtTool
) -> Partitions[OdinPackageFieldSet, BatchMetadata]:
    if odinfmt.skip:
        return Partitions()

    directories = {}

    for field_set in request.field_sets:
        source_path = field_set.address.spec_path

        assert source_path not in directories, "can only have one `odin_package` per directory."
        directories[source_path] = field_set

    return Partitions(Partition([v], metadata=BatchMetadata(d)) for d, v in directories.items())


@rule(level=LogLevel.DEBUG, desc="Format Odin packages with odinfmt")
async def odin_package_fmt(
    request: OdinPackageFmtRequest.Batch[OdinPackageFieldSet, BatchMetadata],
    odinfmt: OdinfmtTool,
    platform: Platform,
) -> FmtResult:
    build_odinfmt_get = Get(BuildOdinfmtResult, BuildOdinfmtRequest, BuildOdinfmtRequest(platform))

    # Get the dependencies of the odin_package targets to find the source files
    dependencies_gets = [
        Get(Targets, DependenciesRequest, DependenciesRequest(field_set.dependencies))
        for field_set in request.elements
    ]

    build_result, *all_dependencies = await MultiGet(build_odinfmt_get, *dependencies_gets)

    # Collect all source files from the dependencies
    source_field_sets = []
    for dependencies in all_dependencies:
        for target in dependencies:
            if not target.has_field(OdinSourceField):
                continue

            source_field_sets.append(target[OdinSourceField])

    if not source_field_sets:
        # No source files found, nothing to format
        return FmtResult.create(request, FallibleProcessResult((), 0, b"", b""), [], [])

    # Get the source files
    sources_digest = await Get(SourceFiles, SourceFilesRequest(source_field_sets))

    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                build_result.digest,
                sources_digest.snapshot.digest,
            ]
        ),
    )

    # Format each odin file individually
    source_files = sources_digest.snapshot.files
    odin_files = [f for f in source_files if f.endswith('.odin')]
    
    if not odin_files:
        return FmtResult.create(request, FallibleProcessResult((), 0, b"", b""), [], [])

    # Run odinfmt on each file
    argv = [build_result.exe_path] + odin_files

    process_result = await Get(
        FallibleProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            description=f"Run odinfmt on {request.partition_metadata.directory}",
            output_files=odin_files,
        ),
    )

    return FmtResult.create(request, process_result, odin_files, [])


def rules():
    return [
        *collect_rules(),
        *OdinPackageFmtRequest.rules(),
    ]