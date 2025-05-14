# Copyright 2022 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.engine.internals.selectors import Get
from pants.engine.process import ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.target_types import (
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
    ImageEnvironment,
    ImageExtractMarker,
)
from pants_backend_oci.util_rules.build_image_artifact import (
    ImageArtifactBuildRequest,
    ImageArtifactExtractRequest,
)
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact


@dataclass(frozen=True)
class ImageLayerPackageFieldSet(PackageFieldSet):
    required_fields = (ImageBase, ImageBuildCommand, ImageBuildOutputs)

    base: ImageBase

    commands: ImageBuildCommand
    outputs: ImageBuildOutputs

    dependencies: ImageDependencies
    environment: ImageEnvironment
    output_path: OutputPathField


@dataclass(frozen=True)
class ImageExtractPackageFieldSet(PackageFieldSet):
    required_fields = (ImageBase, ImageBuildOutputs, ImageExtractMarker)

    base: ImageBase
    outputs: ImageBuildOutputs
    output_path: OutputPathField


@rule(desc="Package OCI Image", level=LogLevel.DEBUG)
async def package_oci_layer(field_set: ImageLayerPackageFieldSet) -> BuiltPackage:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(field_set.address, description_of_origin="Package OCI layer"),
    )

    target = wrapped_target.target

    output = await Get(
        ProcessResult,
        ImageArtifactBuildRequest(ImageArtifactBuildRequest.field_set_type.create(target)),
    )

    artifact = BuiltLayerArtifact(
        relpath=field_set.output_path.value_or_default(file_ending="tar.gz"),
        extra_log_lines=(f"Built artifacts: {output.output_digest}",),
    )

    return BuiltPackage(output.output_digest, (artifact,))


@rule(desc="Package OCI Image", level=LogLevel.DEBUG)
async def package_oci_layer_extract(field_set: ImageExtractPackageFieldSet) -> BuiltPackage:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(field_set.address, description_of_origin="Package OCI layer"),
    )

    target = wrapped_target.target

    output = await Get(
        ProcessResult,
        ImageArtifactExtractRequest(ImageArtifactExtractRequest.field_set_type.create(target)),
    )

    artifact = BuiltLayerArtifact(
        relpath=field_set.output_path.value_or_default(file_ending="tar.gz"),
        extra_log_lines=(f"Built artifacts: {output.output_digest}",),
    )

    return BuiltPackage(output.output_digest, (artifact,))


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ImageLayerPackageFieldSet),
        UnionRule(PackageFieldSet, ImageExtractPackageFieldSet),
    ]
