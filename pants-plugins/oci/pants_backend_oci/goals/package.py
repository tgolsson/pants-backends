from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import (
    BuiltPackage,
    BuiltPackageArtifact,
    OutputPathField,
    PackageFieldSet,
)
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.graph import resolve_target
from pants.engine.internals.selectors import Get
from pants.engine.intrinsics import merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import InvalidTargetException, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import SkopeoTool
from pants_backend_oci.target_types import ImageBuildOutputs, ImageDigest, ImageRepository, ImageTag
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
)


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
    description: str


@dataclass(frozen=True)
class BuiltOciArchive(BuiltPackage):
    """An OCI image in `oci-archive` format."""

    directory: str = ""
    tag: str = ""
    sha: str = ""


@rule(desc="Convert OCI Image to Archive", level=LogLevel.DEBUG)
async def package_oci_archive(request: OciArchiveRequest, skopeo: SkopeoTool, platform: Platform) -> Process:
    skopeo = await download_external_tool(skopeo.get_request(platform))

    sandbox_input = await merge_digests(MergeDigests([skopeo.digest, request.input_digest]))
    return Process(
        input_digest=sandbox_input,
        argv=(
            skopeo.exe,
            # TODO[TSOL]: Should likely provide a way to inject a
            # policy into this... Maybe dependency injector?
            "--insecure-policy",
            "copy",
            "oci:build:build",
            f"oci:{request.output_filename}",
        ),
        description=f"Generate OCI Archive: {request.output_filename}",
        output_directories=(request.output_filename,),
    )


@dataclass(frozen=True)
class BuiltOciImage(BuiltPackageArtifact):
    # We don't really want a default for this field, but the superclass has a field with
    # a default, so all subsequent fields must have one too. The `create()` method below
    # will ensure that this field is properly populated in practice.
    sha: str = ""
    tag: str = ""

    @classmethod
    def create(cls, sha: str, tag: str, directory: str) -> BuiltOciImage:
        return cls(
            sha=sha,
            tag=tag,
            relpath=directory,
            extra_log_lines=(f"  {cls.__name__}: {directory}@{sha}",),
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
    image = await Get(FallibleImageBundle, FallibleImageBundleRequest, request.request)
    if image.exit_code != 0 or image.dependency_failed:
        raise Exception(
            f"Failed packaging image:\n{image.stderr}",
        )

    image_digest = image.output.digest

    suffix = field_set.tag.value if field_set.tag.value else field_set.digest.value
    archive_process = await package_oci_archive(
        OciArchiveRequest(
            input_digest=image_digest,
            output_filename=field_set.output_path.value_or_default(file_ending="d"),
            description=f"Package OCI Image {field_set.address} -> {field_set.repository.value}:{suffix}",
        ),
        **implicitly(),
    )

    result = await fallible_to_exec_result_or_raise(**implicitly(archive_process))

    return BuiltPackage(
        digest=result.output_digest,
        artifacts=(
            BuiltOciImage.create(
                image.output.image_sha, "build", field_set.output_path.value_or_default(file_ending="d")
            ),
        ),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ImageFieldSet),
    ]
