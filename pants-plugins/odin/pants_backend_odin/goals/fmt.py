from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.partitions import Partition, PartitionerType, Partitions
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
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
        partitions.append(
            Partition(
                frozenset([field_set.source.file_path]),
                PackageMetadata(
                    address=field_set.address,
                ),
            )
        )

    return Partitions(partitions)


@rule
async def odin_source_fmt(
    request: OdinSourceFmtRequest.Batch[OdinSourceFmtFieldSet],
    odinfmt: OdinfmtTool,
    platform: Platform,
) -> FmtResult:
    build_odinfmt_result = await Get(BuildOdinfmtResult, BuildOdinfmtRequest, BuildOdinfmtRequest(platform))

    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                build_odinfmt_result.digest,
                request.snapshot.digest,
            ]
        ),
    )

    # Run odinfmt on the files
    argv = [build_odinfmt_result.exe_path, "-w"] + list(request.files)

    process_result = await Get(
        ProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            description="Format Odin files with odinfmt",
            output_files=request.files,
        ),
    )

    return await FmtResult.create(request, process_result)


def rules():
    return [
        *collect_rules(),
        *OdinSourceFmtRequest.rules(),
    ]
