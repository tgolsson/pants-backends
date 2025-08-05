from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.partitions import Partition, PartitionerType, Partitions
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinfmtTool
from pants_backend_odin.target_types import OdinSourceField
from pants_backend_odin.util_rules.build import BuildOdinfmtRequest, BuildOdinfmtResult


@dataclass(frozen=True)
class OdinSourceFmtFieldSet(FieldSet):
    required_fields = (OdinSourceField,)

    source: OdinSourceField


@dataclass(frozen=True)
class OdinSourceFmtRequest(FmtTargetsRequest):
    field_set_type = OdinSourceFmtFieldSet
    partitioner_type = PartitionerType.CUSTOM
    tool_subsystem = OdinfmtTool


@dataclass(frozen=True)
class PackageMetadata:
    address: Address

    @property
    def description(self) -> None:
        return None


@rule(level=LogLevel.DEBUG, desc="Partition Odin source files for formatting")
async def partition_odin_sources(
    request: OdinSourceFmtRequest.PartitionRequest[OdinSourceFmtFieldSet], odinfmt: OdinfmtTool
) -> Partitions[OdinSourceFmtFieldSet, None]:
    if odinfmt.skip:
        return Partitions()

    partitions = []
    for field_set in request.field_sets:
        source_files = await Get(
            SourceFiles,
            SourceFilesRequest([field_set.source]),
        )

        partitions.append(
            Partition(
                frozenset([f for f in source_files.files if f.endswith("odin")]),
                PackageMetadata(
                    address=field_set.address,
                ),
            )
        )

    return Partitions(partitions)


@rule(level=LogLevel.DEBUG, desc="Format Odin source files with odinfmt")
async def odin_source_fmt(
    request: OdinSourceFmtRequest.Batch[OdinSourceFmtFieldSet],
    odinfmt: OdinfmtTool,
    platform: Platform,
) -> FmtResult:
    print("yy")
    build_odinfmt_result = await Get(BuildOdinfmtResult, BuildOdinfmtRequest, BuildOdinfmtRequest(platform))

    print(request.elements)
    # Get the source files to format

    source_files = await Get(SourceFiles, SourceFilesRequest([field_set for field_set in request.elements]))

    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                build_odinfmt_result.digest,
                source_files.snapshot.digest,
            ]
        ),
    )

    # Run odinfmt on the files
    argv = [build_odinfmt_result.exe_path] + list(source_files.snapshot.files)

    process_result = await Get(
        FallibleProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            description=f"Format Odin files with odinfmt",
            output_files=source_files.snapshot.files,
        ),
    )

    return FmtResult.create(request, process_result, source_files.snapshot.files, [])


def rules():
    return [
        *collect_rules(),
        *OdinSourceFmtRequest.rules(),
    ]
