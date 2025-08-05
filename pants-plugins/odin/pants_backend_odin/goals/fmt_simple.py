from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.partitions import Partition, Partitions
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest
from pants.engine.internals.selectors import Get
from pants.engine.process import FallibleProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinfmtTool
from pants_backend_odin.target_types import OdinSourceField


@dataclass(frozen=True, order=True)
class OdinSourceFmtFieldSet(FieldSet):
    required_fields = (OdinSourceField,)

    source: OdinSourceField


class OdinSourceFmtRequest(FmtTargetsRequest):
    field_set_type = OdinSourceFmtFieldSet
    tool_subsystem = OdinfmtTool


@rule(level=LogLevel.DEBUG, desc="Partition Odin source files for formatting")
async def partition_odin_sources(
    request: OdinSourceFmtRequest.PartitionRequest[OdinSourceFmtFieldSet], odinfmt: OdinfmtTool
) -> Partitions[OdinSourceFmtFieldSet, None]:
    if odinfmt.skip:
        return Partitions()

    # Format each Odin source file individually
    return Partitions(Partition([field_set], metadata=None) for field_set in request.field_sets)


@rule(level=LogLevel.DEBUG, desc="Format Odin source files with odinfmt")
async def odin_source_fmt(
    request: OdinSourceFmtRequest.Batch[OdinSourceFmtFieldSet, None],
    odinfmt: OdinfmtTool,
) -> FmtResult:
    if odinfmt.skip:
        return FmtResult.skip(formatter_name="odinfmt")

    # Get the source files to format
    source_files = await Get(
        SourceFiles,
        SourceFilesRequest([field_set.source for field_set in request.elements])
    )

    # For now, just return the original files without any changes
    # This is just to test if the interface works
    process_result = FallibleProcessResult((), 0, b"", b"")

    return FmtResult.create(request, process_result, source_files.snapshot.files, [])


def rules():
    return [
        *collect_rules(),
        *OdinSourceFmtRequest.rules(),
    ]