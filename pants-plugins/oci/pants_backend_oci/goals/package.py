from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import (
    BuiltPackage,
    BuiltPackageArtifact,
    OutputPathField,
    PackageFieldSet,
)
from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import InvalidTargetException, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.target_types import ImageBuildOutputs, ImageDigest, ImageRepository, ImageTag
from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules


@dataclass(frozen=True)
class ImageFieldSet(PackageFieldSet):
    required_fields = (ImageRepository,)

    repository: ImageRepository
    tag: ImageTag

    output_path: OutputPathField

    digest: ImageDigest


@dataclass(frozen=True)
class ArtifactFieldSet(PackageFieldSet):
    required_fields = (ImageBuildOutputs,)

    outputs: ImageBuildOutputs
    output_path: OutputPathField


@dataclass(frozen=True)
class OciArchiveRequest:
    input_digest: Digest
    output_filename: str
    name: str | None
    description: str


@rule(desc="Convert OCI Image to Archive", level=LogLevel.DEBUG)
async def package_oci_archive(request: OciArchiveRequest, python: PythonBuildStandaloneBinary) -> Process:
    builder = await create_digest(sandboxed_modules(oci_layout_builder))
    sandbox_input = await merge_digests(MergeDigests([builder, request.input_digest]))
    name = ("--name", request.name) if request.name else ()
    return Process(
        input_digest=sandbox_input,
        argv=(python.path, "-c", module_main(oci_layout_builder), "export", request.output_filename, *name),
        description=request.description,
        output_files=(request.output_filename,),
    )


@dataclass(frozen=True)
class BuiltOciImage(BuiltPackageArtifact):
    # We don't really want a default for this field, but the superclass has a field with
    # a default, so all subsequent fields must have one too. The `create()` method below
    # will ensure that this field is properly populated in practice.
    sha: str = ""
    tag: str = ""

    @classmethod
    def create(cls, sha: str, tag: str, path: str) -> BuiltOciImage:
        return cls(
            sha=sha,
            tag=tag,
            relpath=path,
            extra_log_lines=(f"  {cls.__name__}: {path}@{sha}",),
        )


@rule(desc="Package OCI Image", level=LogLevel.DEBUG)
async def package_oci_image(field_set: ImageFieldSet) -> BuiltPackage:
    wrapped_target = await resolve_target(
        WrappedTargetRequest(field_set.address, description_of_origin="package_oci_image"), **implicitly()
    )
    target = wrapped_target.target
    if field_set.tag.value is not None and field_set.digest.value is not None:
        raise InvalidTargetException(
            f"The {repr(target.alias)} target {field_set.address} must specify one of `digest` and"
            f" `tag` but not both: {field_set.tag} {field_set.digest}."
        )

    request = await ibr_to_fibr(ImageBundleRequest(target), **implicitly())
    image = await resolve_image_bundle(**implicitly({request.request: FallibleImageBundleRequest}))
    if image.exit_code != 0 or image.dependency_failed:
        raise Exception(
            f"Failed packaging image:\n{image.stderr}",
        )

    image_digest = image.output.digest

    suffix = field_set.tag.value if field_set.tag.value else field_set.digest.value
    # A pulled image is named by its registry digest, which normalising it to OCI may have changed,
    # so only a tagged image gets a name.
    name = f"{field_set.repository.value}:{field_set.tag.value}" if field_set.tag.value else None
    output_filename = field_set.output_path.value_or_default(file_ending="tar")
    archive_process = await package_oci_archive(
        OciArchiveRequest(
            input_digest=image_digest,
            output_filename=output_filename,
            name=name,
            description=f"Package OCI Image {field_set.address} -> {field_set.repository.value}:{suffix}",
        ),
        **implicitly(),
    )

    result = await fallible_to_exec_result_or_raise(**implicitly(archive_process))

    return BuiltPackage(
        digest=result.output_digest,
        artifacts=(
            BuiltOciImage.create(image.output.image_sha, field_set.tag.value or "build", output_filename),
        ),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ImageFieldSet),
    ]
