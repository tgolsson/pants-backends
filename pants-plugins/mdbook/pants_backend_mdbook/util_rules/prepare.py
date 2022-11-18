import os
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    InvalidTargetException,
    SourcesField,
    Targets,
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants_backend_mdbook.subsystem import MdBookTool
from pants_backend_mdbook.targets import MdBookSources


@dataclass(frozen=True)
class MdBookAnalysis:
    digest: Digest
    build_root: str
    tool_exe: str


@dataclass(frozen=True)
class MdBookAnalysisRequest:
    address: Address


@rule(desc="Prepare MD Book Build Context")
async def prepare_md_book_ctx(request: MdBookAnalysisRequest, mdbook: MdBookTool) -> MdBookAnalysis:
    (targets, transitive_targets) = await MultiGet(
        Get(Targets, Addresses([request.address])),
        Get(TransitiveTargets, TransitiveTargetsRequest([request.address])),
    )

    target = targets[0]

    (sources, tool) = await MultiGet(
        Get(
            SourceFiles,
            SourceFilesRequest(
                [target.get(MdBookSources)]
                + [t.get(SourcesField) for t in transitive_targets.dependencies],
            ),
        ),
        Get(
            DownloadedExternalTool,
            ExternalToolRequest,
            mdbook.get_request(Platform.current),
        ),
    )

    build_root = None
    for s in sources.files:
        if s.endswith("book.toml"):
            build_root = os.path.dirname(s)

    if build_root is None:
        raise InvalidTargetException("Must include a `book.toml` in the `md_book` sources.")

    sandbox_input = await Get(Digest, MergeDigests([tool.digest, sources.snapshot.digest]))
    return MdBookAnalysis(sandbox_input, build_root, tool.exe)


def rules():
    return collect_rules()
