"""Shared by the tests: `cc_toolchain` BUILD snippets; `toy_cc`, a compile-step provider that
compiles each of its `sources` in the sandbox root with the toolchain's compiler; and `test_gen`,
a stand-in code generator, so codegen dependencies are tested without `pants-backend-generate`.
`toy_library.py` has a library backend."""

from __future__ import annotations

from dataclasses import dataclass

from pants.backend.cc.target_types import CCSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import CreateDigest, FileContent
from pants.engine.intrinsics import create_digest, digest_to_snapshot, get_digest_contents
from pants.engine.rules import implicitly, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    GeneratedSources,
    GenerateSourcesRequest,
    MultipleSourcesField,
    Target,
)
from pants.engine.unions import UnionRule

from pants_backend_cc.util_rules.compile import (
    CompileInputs,
    CompileInputsRequest,
    CompileStep,
    CompileSteps,
    CompileStepsFieldSet,
)
from pants_backend_cc.util_rules.toolchain import CcToolchainRequest, resolve_cc_toolchain
from pants_backend_platforms.testutil import HOST, OTHER
from pants_backend_platforms.util_rules.platforms import TargetPlatform


def toolchain_build(name: str, target: str, *, host: str = HOST, cc: str = "bash", ar: str = "bash") -> str:
    """A `cc_toolchain` whose tools default to `bash`, which answers `--version` everywhere."""
    return (
        f'cc_toolchain(name="{name}", host="{host}", target="{target}", cc="{cc}", cxx="bash", ar="{ar}")\n'
    )


def toolchains_build() -> str:
    """A `cc_toolchain` from the host to itself and to Windows."""
    return toolchain_build(f"to-{HOST}", HOST) + toolchain_build(f"to-{OTHER}", OTHER)


class ToyCcSourcesField(MultipleSourcesField):
    expected_file_extensions = (".c", ".cpp", ".h")


class ToyCcTarget(Target):
    alias = "toy_cc"
    core_fields = (*COMMON_TARGET_FIELDS, ToyCcSourcesField)
    help = "Compiles each translation unit in `sources` on its own, from the sandbox root."


@dataclass(frozen=True)
class ToyCompileStepsFieldSet(CompileStepsFieldSet):
    required_fields = (ToyCcSourcesField,)

    sources: ToyCcSourcesField


@dataclass(frozen=True)
class ToyCompileInputsRequest(CompileInputsRequest):
    sources: ToyCcSourcesField


@rule
async def toy_compile_steps(field_set: ToyCompileStepsFieldSet, platform: TargetPlatform) -> CompileSteps:
    toolchain = await resolve_cc_toolchain(CcToolchainRequest(platform), **implicitly())
    files = await determine_source_files(SourceFilesRequest([field_set.sources]))
    steps = []
    for source in files.files:
        if source.endswith(".h"):
            continue
        language, compiler = ("c", toolchain.cc) if source.endswith(".c") else ("cxx", toolchain.cxx)
        steps.append(
            CompileStep(
                owner=field_set.address,
                source=source,
                generated=False,
                object=f"{source}.o",
                language=language,
                argv=(compiler.path, "-I.", "-c", source, "-o", f"{source}.o"),
                cwd="",
                env=toolchain.env,
                inputs=ToyCompileInputsRequest(field_set.sources),
            )
        )
    return CompileSteps(steps)


@rule
async def toy_compile_inputs(request: ToyCompileInputsRequest) -> CompileInputs:
    files = await determine_source_files(SourceFilesRequest([request.sources]))
    return CompileInputs(files.snapshot.digest)


def toy_cc_rules() -> list:
    return [
        toy_compile_steps,
        toy_compile_inputs,
        UnionRule(CompileStepsFieldSet, ToyCompileStepsFieldSet),
        UnionRule(CompileInputsRequest, ToyCompileInputsRequest),
    ]


@dataclass(frozen=True)
class ToyShadowFieldSet(CompileStepsFieldSet):
    """A second member for `toy_cc`, which consumers reject."""

    required_fields = (ToyCcSourcesField,)


@rule
async def toy_shadow_compile_steps(field_set: ToyShadowFieldSet, platform: TargetPlatform) -> CompileSteps:
    raise AssertionError("never dispatched: a target with two members is rejected")


SHADOW_RULES = (toy_shadow_compile_steps, UnionRule(CompileStepsFieldSet, ToyShadowFieldSet))


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
