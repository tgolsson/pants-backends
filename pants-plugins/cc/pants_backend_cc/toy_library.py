"""`toy_library`, a library backend without a build system for the tests: it archives (or links
into a shared library) the C files of its `sources` under `toybuild/<directory>`, and lets
consumers work in any directory unless its `cwd` says otherwise. A coverage consumer gets it
compiled with `--coverage`."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.addresses import Address
from pants.engine.fs import (
    EMPTY_DIGEST,
    CreateDigest,
    Digest,
    DigestSubset,
    FileContent,
    MergeDigests,
    PathGlobs,
)
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import create_digest, digest_subset_to_digest, merge_digests
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import concurrently, implicitly, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    BoolField,
    MultipleSourcesField,
    StringField,
    StringSequenceField,
    Target,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule

from pants_backend_cc.util_rules.compile import C
from pants_backend_cc.util_rules.library import (
    CcCompileInterface,
    CcConsumer,
    CcLibraryCwd,
    CcLibraryError,
    CcLibraryFieldSet,
    CcLinkInterface,
)
from pants_backend_cc.util_rules.toolchain import CcToolchainRequest, resolve_cc_toolchain
from pants_backend_platforms.util_rules.platforms import TargetPlatform

TOY_BUILD_DIR = "toybuild"


class ToySourcesField(MultipleSourcesField):
    expected_file_extensions = (".c", ".h")


class ToySharedField(BoolField):
    alias = "shared"
    default = False
    help = "A shared library, with a `lib<name>.data` file it needs at run time."


class ToyLinkWithField(StringSequenceField):
    alias = "link_with"
    default = ()
    help = "`toy_library` targets in this directory whose libraries follow this one's on the link line."


class ToyLinkFlagsField(StringSequenceField):
    alias = "link_flags"
    default = ()
    help = "Link flags consumers get."


class ToyLanguagesField(StringSequenceField):
    alias = "languages"
    default = ("c", "cxx")
    help = "The consumer languages served."


class ToyCwdField(StringField):
    alias = "cwd"
    help = "The working directory consumers must use; unset, any. Absolute is an error."


class ToyOutputDirField(StringField):
    alias = "output_dir"
    help = "Where the library is built; unset, `toybuild/<this directory>`."


class ToyLibraryTarget(Target):
    alias = "toy_library"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ToySourcesField,
        ToySharedField,
        ToyLinkWithField,
        ToyLinkFlagsField,
        ToyLanguagesField,
        ToyCwdField,
        ToyOutputDirField,
    )
    help = "A C library for the tests; consumers get `-I` of its directory."


@dataclass(frozen=True)
class ToyLibraryFieldSet(CcLibraryFieldSet):
    required_fields = (ToySourcesField,)

    sources: ToySourcesField
    link_with: ToyLinkWithField
    link_flags: ToyLinkFlagsField
    languages: ToyLanguagesField
    cwd: ToyCwdField


@dataclass(frozen=True)
class ToyArchiveRequest:
    address: Address
    platform: TargetPlatform
    coverage: bool = False


@dataclass(frozen=True)
class ToyArchive:
    path: str
    """The library, relative to the sandbox root."""
    digest: Digest
    runtime: Digest
    coverage: Digest
    """For a coverage build, the `.gcno` notes and the sources."""


@rule
async def toy_archive(request: ToyArchiveRequest, bash: BashBinary) -> ToyArchive:
    wrapped = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="toy_library"), **implicitly()
    )
    target = wrapped.target
    toolchain, files = await concurrently(
        resolve_cc_toolchain(CcToolchainRequest(request.platform), **implicitly()),
        determine_source_files(SourceFilesRequest([target[ToySourcesField]])),
    )
    name = request.address.target_name
    shared = target[ToySharedField].value
    build_dir = os.path.normpath(
        target[ToyOutputDirField].value or os.path.join(TOY_BUILD_DIR, request.address.spec_path)
    )
    units = [u for u in files.files if u.endswith(".c")]
    objects = [f"{name}_{os.path.basename(u)}.o" for u in units]
    library = f"lib{name}.so" if shared else f"lib{name}.a"
    pic = ("-fPIC",) if shared else ()
    instrument = ("--coverage",) if request.coverage else ()
    commands = [
        shlex.join((toolchain.cc.path, *pic, *instrument, "-c", os.path.relpath(u, build_dir), "-o", o))
        for u, o in zip(units, objects)
    ]
    notes = [f"{build_dir}/{o[: -len('.o')]}.gcno" for o in objects] if request.coverage else []
    if shared:
        commands.append(shlex.join((toolchain.cc.path, "-shared", *instrument, "-o", library, *objects)))
    else:
        commands.append(shlex.join((toolchain.ar.path, "rcs", library, *objects)))
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(
                    bash.path,
                    "-c",
                    f"mkdir -p {build_dir} && cd {build_dir} && {' && '.join(commands)}",
                ),
                description=f"Build {request.address}",
                input_digest=files.snapshot.digest,
                env=toolchain.env,
                output_files=(f"{build_dir}/{library}", *notes),
            )
        )
    )
    built = await digest_subset_to_digest(
        DigestSubset(result.output_digest, PathGlobs([f"{build_dir}/{library}"]))
    )
    coverage = EMPTY_DIGEST
    if notes:
        coverage = await merge_digests(
            MergeDigests(
                [
                    await digest_subset_to_digest(DigestSubset(result.output_digest, PathGlobs(notes))),
                    files.snapshot.digest,
                ]
            )
        )
    runtime = EMPTY_DIGEST
    if shared:
        data = await create_digest(
            CreateDigest([FileContent(f"{build_dir}/lib{name}.data", f"{name}\n".encode())])
        )
        runtime = await merge_digests(MergeDigests([built, data]))
    return ToyArchive(f"{build_dir}/{library}", built, runtime, coverage)


def _serves(field_set: ToyLibraryFieldSet, consumer: CcConsumer) -> None:
    languages = field_set.languages.value or ()
    if consumer.language not in languages:
        raise CcLibraryError(
            f"{field_set.address} has no {consumer.language} consumers: its `languages` are {list(languages)}"
        )


def _from(cwd: str | None, path: str) -> str:
    return os.path.relpath(path, cwd or ".")


@rule
async def toy_library_cwd(
    field_set: ToyLibraryFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcLibraryCwd:
    cwd = field_set.cwd.value
    if cwd is not None and os.path.isabs(cwd):
        raise CcLibraryError(f"{field_set.address}: `cwd` is relative to the sandbox root, not `{cwd}`")
    return CcLibraryCwd(cwd)


@rule
async def toy_compile_interface(
    field_set: ToyLibraryFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcCompileInterface:
    _serves(field_set, consumer)
    files = await determine_source_files(SourceFilesRequest([field_set.sources]))
    return CcCompileInterface(
        flags=(f"-I{_from(consumer.cwd, field_set.address.spec_path or '.')}",),
        headers=files.snapshot.digest,
        files=files.snapshot.digest,
        file_paths=files.files,
        generated=EMPTY_DIGEST,
    )


@rule
async def toy_link_interface(
    field_set: ToyLibraryFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcLinkInterface:
    _serves(field_set, consumer)
    addresses = [
        field_set.address,
        *(Address(field_set.address.spec_path, target_name=n) for n in field_set.link_with.value or ()),
    ]
    archives = await concurrently(
        toy_archive(ToyArchiveRequest(a, platform, consumer.coverage), **implicitly()) for a in addresses
    )
    libraries, runtime, coverage = await concurrently(
        merge_digests(MergeDigests(a.digest for a in archives)),
        merge_digests(MergeDigests(a.runtime for a in archives)),
        merge_digests(MergeDigests(a.coverage for a in archives)),
    )
    shared = [a.path for a in archives if a.path.endswith(".so")]
    return CcLinkInterface(
        driver=C,
        flags=tuple(field_set.link_flags.value or ()),
        args=tuple(_from(consumer.cwd, a.path) for a in archives),
        libraries=libraries,
        runtime=runtime,
        runtime_dirs=tuple(dict.fromkeys(_from(consumer.cwd, os.path.dirname(p)) for p in shared)),
        coverage=coverage,
    )


@dataclass(frozen=True)
class ToyLibraryShadowFieldSet(CcLibraryFieldSet):
    """A second library field set for `toy_library`, which consumers reject."""

    required_fields = (ToySourcesField,)


@rule
async def toy_shadow_cwd(
    field_set: ToyLibraryShadowFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcLibraryCwd:
    raise AssertionError("never dispatched: a target with two members is rejected")


@rule
async def toy_shadow_compile(
    field_set: ToyLibraryShadowFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcCompileInterface:
    raise AssertionError("never dispatched: a target with two members is rejected")


@rule
async def toy_shadow_link(
    field_set: ToyLibraryShadowFieldSet, platform: TargetPlatform, consumer: CcConsumer
) -> CcLinkInterface:
    raise AssertionError("never dispatched: a target with two members is rejected")


LIBRARY_SHADOW_RULES = (
    toy_shadow_cwd,
    toy_shadow_compile,
    toy_shadow_link,
    UnionRule(CcLibraryFieldSet, ToyLibraryShadowFieldSet),
)


def toy_library_rules() -> list:
    return [
        toy_archive,
        toy_library_cwd,
        toy_compile_interface,
        toy_link_interface,
        UnionRule(CcLibraryFieldSet, ToyLibraryFieldSet),
    ]
