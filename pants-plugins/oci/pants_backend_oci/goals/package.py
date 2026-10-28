from __future__ import annotations

import json
from dataclasses import dataclass

from pants.core.goals.package import (
    BuiltPackage,
    BuiltPackageArtifact,
    OutputPathField,
    PackageFieldSet,
)
from pants.engine.fs import (
    AddPrefix,
    Digest,
    DigestSubset,
    GlobExpansionConjunction,
    GlobMatchErrorBehavior,
    MergeDigests,
    PathGlobs,
    RemovePrefix,
)
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import (
    add_prefix,
    digest_subset_to_digest,
    merge_digests,
    remove_prefix,
)
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import InvalidTargetException, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciPackageOutput, OciSubsystem
from pants_backend_oci.target_types import ImageBuildOutputs, ImageDigest, ImageRepository, ImageTag
from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundleRequest,
    ImageBundle,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder


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
async def package_oci_archive(request: OciArchiveRequest, builder: OciBuilder) -> Process:
    name = ("--name", request.name) if request.name else ()
    return builder.process(
        oci_layout_builder,
        "export",
        request.output_filename,
        *name,
        input_digest=request.input_digest,
        description=request.description,
        output_files=(request.output_filename,),
    )


@dataclass(frozen=True)
class OciLayoutRequest:
    bundle: ImageBundle
    output_dir: str
    name: str | None
    description: str


@rule(desc="Convert OCI Image to Layout", level=LogLevel.DEBUG)
async def package_oci_layout(request: OciLayoutRequest, builder: OciBuilder) -> Digest:
    """The image as an OCI layout under `output_dir`, holding only its own blobs.

    The process sees only the image's metadata and writes the new index. The blobs are taken from
    the image's layout as they are, so a large image is packaged without writing or storing a copy.
    """
    name = ("--name", request.name) if request.name else ()
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            builder.process(
                oci_layout_builder,
                "export-layout",
                request.output_dir,
                *name,
                input_digest=request.bundle.metadata,
                description=request.description,
                output_files=(f"{request.output_dir}/index.json", f"{request.output_dir}/oci-layout"),
            )
        )
    )
    image = json.loads(result.stdout)
    blobs = await digest_subset_to_digest(
        DigestSubset(
            request.bundle.digest,
            PathGlobs(
                [f"build/blobs/{digest.replace(':', '/', 1)}" for digest in image["blobs"]],
                glob_match_error_behavior=GlobMatchErrorBehavior.error,
                conjunction=GlobExpansionConjunction.all_match,
                description_of_origin=request.description,
            ),
        )
    )
    blobs = await add_prefix(AddPrefix(await remove_prefix(RemovePrefix(blobs, "build")), request.output_dir))
    return await merge_digests(MergeDigests([result.output_digest, blobs]))


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
async def package_oci_image(field_set: ImageFieldSet, oci: OciSubsystem) -> BuiltPackage:
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
    description = f"Package OCI Image {field_set.address} -> {field_set.repository.value}:{suffix}"
    if oci.package_output == OciPackageOutput.layout:
        output_path = field_set.output_path.value_or_default(file_ending=None)
        digest = await package_oci_layout(
            OciLayoutRequest(image.output, output_path, name, description), **implicitly()
        )
    else:
        output_path = field_set.output_path.value_or_default(file_ending="tar")
        archive_process = await package_oci_archive(
            OciArchiveRequest(
                input_digest=image_digest,
                output_filename=output_path,
                name=name,
                description=description,
            ),
            **implicitly(),
        )
        digest = (await fallible_to_exec_result_or_raise(**implicitly(archive_process))).output_digest

    return BuiltPackage(
        digest=digest,
        artifacts=(
            BuiltOciImage.create(image.output.image_sha, field_set.tag.value or "build", output_path),
        ),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ImageFieldSet),
    ]
