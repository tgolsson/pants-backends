import os
from dataclasses import dataclass

from pants.core.target_types import FileSourceField
from pants.core.util_rules.external_tool import download_external_tool
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.graph import resolve_targets
from pants.engine.intrinsics import merge_digests
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    InvalidTargetException,
    SourcesField,
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
async def prepare_md_book_ctx(
    request: MdBookAnalysisRequest, mdbook: MdBookTool, platform: Platform
) -> MdBookAnalysis:
    target = await resolve_targets(
        **implicitly(
            Addresses([request.address]),
        )
    )
    target = target[0]
    dependencies = await resolve_targets(
        **implicitly(
            DependenciesRequest(target[Dependencies]),
        )
    )

    (sources, codegened, tool) = await concurrently(
        determine_source_files(
            SourceFilesRequest(
                [target.get(MdBookSources)] + [t.get(SourcesField) for t in dependencies],
            )
        ),
        determine_source_files(
            SourceFilesRequest(
                [t.get(SourcesField) for t in dependencies],
                for_sources_types=(FileSourceField, MdBookSources),
                enable_codegen=True,
            )
        ),
        download_external_tool(mdbook.get_request(platform)),
    )

    build_root = None
    for s in sources.files:
        if s.endswith("book.toml"):
            build_root = os.path.dirname(s)

    if build_root is None:
        raise InvalidTargetException("Must include a `book.toml` in the `md_book` sources.")

    sandbox_input = await merge_digests(
        MergeDigests([tool.digest, sources.snapshot.digest, codegened.snapshot.digest])
    )
    return MdBookAnalysis(sandbox_input, build_root, tool.exe)


def rules():
    return collect_rules()
