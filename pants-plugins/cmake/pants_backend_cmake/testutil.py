"""Shared by the integration tests: a stand-in code generator, so the codegen dependencies of
`cmake_project` and `cmake_test` are tested without `pants-backend-generate`."""

from __future__ import annotations

from pants.backend.cc.target_types import CCSourceField
from pants.engine.fs import CreateDigest, FileContent
from pants.engine.intrinsics import create_digest, digest_to_snapshot, get_digest_contents
from pants.engine.rules import rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    GeneratedSources,
    GenerateSourcesRequest,
    MultipleSourcesField,
    Target,
)
from pants.engine.unions import UnionRule


class GenSourcesField(MultipleSourcesField):
    expected_file_extensions = (".in",)


class GenTarget(Target):
    alias = "test_gen"
    core_fields = (*COMMON_TARGET_FIELDS, GenSourcesField)
    help = "Generates `<path>` from every `<path>.in` in `sources`, verbatim: C/C++ codegen."


class GenRequest(GenerateSourcesRequest):
    input = GenSourcesField
    output = CCSourceField


@rule
async def generate_from_templates(request: GenRequest) -> GeneratedSources:
    contents = await get_digest_contents(request.protocol_sources.digest)
    digest = await create_digest(
        CreateDigest([FileContent(fc.path[: -len(".in")], fc.content) for fc in contents])
    )
    return GeneratedSources(await digest_to_snapshot(digest))


def codegen_rules() -> list:
    return [generate_from_templates, UnionRule(GenerateSourcesRequest, GenRequest)]
