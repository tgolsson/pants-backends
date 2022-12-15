# Copyright 2022 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import InvalidTargetException, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import SkopeoTool
from pants_backend_oci.target_types import ImageDigest, ImageRepository, ImageTag
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundleRequest,
)


@dataclass(frozen=True)
class ImageFieldSet(PackageFieldSet):
    required_fields = (ImageRepository,)

    repository: ImageRepository
    tag: ImageTag
    digest: ImageDigest


@dataclass(frozen=True)
class OciArchiveRequest:
    input_digest: Digest
    output_filename: str
    description: str


@dataclass(frozen=True)
class OciArchive:
    """An OCI image in `oci-archive` format."""

    digest: Digest


@rule(desc="Convert OCI Image to Archive", level=LogLevel.DEBUG)
async def package_oci_archive(
    request: OciArchiveRequest, skopeo: SkopeoTool, platform: Platform
) -> OciArchive:
    skopeo = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        skopeo.get_request(platform),
    )

    sandbox_input = await Get(Digest, MergeDigests([skopeo.digest, request.input_digest]))
    result = await Get(
        ProcessResult,
        Process(
            input_digest=sandbox_input,
            argv=(
                skopeo.exe,
                # TODO[TSOL]: Should likely provide a way to inject a
                # policy into this... Maybe dependency injector?
                "--insecure-policy",
                "copy",
                "oci:build:build",
                f"oci-archive:{request.output_filename}",
            ),
            description=f"Generate OCI Archive: {request.output_filename}",
            output_files=(request.output_filename,),
        ),
    )

    return OciArchive(result.output_digest)


@rule(desc="Package OCI Image", level=LogLevel.DEBUG)
async def package_oci_image(field_set: ImageFieldSet) -> BuiltPackage:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(field_set.address, description_of_origin="package_oci_image"),
    )
    target = wrapped_target.target
    if field_set.tag.value is not None and field_set.digest.value is not None:
        raise InvalidTargetException(
            f"The {repr(target.alias)} target {field_set.address} must specify one of `digest` and"
            f" `tag` but not both: {field_set.tag} {field_set.digest}."
        )

    request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(target))
    image = await Get(FallibleImageBundle, FallibleImageBundleRequest, request.request)
    if image.exit_code != 0 or image.dependency_failed:
        raise Exception(
            f"Failed packaging image:\n{image.stderr}",
        )

    image_digest = image.output.digest

    name = (
        field_set.repository.value.replace("/", "_").replace(":", "_")
        if field_set.repository.value
        else str(target.address.spec).replace("/", "_").replace(":", "_")
    )
    suffix = field_set.tag.value if field_set.tag.value else field_set.digest.value
    archive = await Get(
        OciArchive,
        OciArchiveRequest(
            input_digest=image_digest,
            output_filename=f"./{name}.{suffix}",
            description=f"Package OCI Image {field_set.address} -> {field_set.repository.value}:{suffix}",
        ),
    )

    artifact = BuiltPackageArtifact(
        relpath=f"{name}.{suffix}",
        extra_log_lines=(f"Packaged image: {image.output.image_sha}",),
    )

    return BuiltPackage(archive.digest, (artifact,))


def rules():
    return [*collect_rules(), UnionRule(PackageFieldSet, ImageFieldSet)]
