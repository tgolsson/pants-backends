""" """

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import ClassVar

from pants.core.goals.package import OutputPathField, PackageFieldSet
from pants.engine.internals.graph import (
    resolve_target,
    resolve_targets,
    resolve_unparsed_address_inputs,
)
from pants.engine.process import ProcessResult
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import DependenciesRequest, FieldSet, Target, WrappedTargetRequest
from pants.util.logging import LogLevel

from pants_backend_oci.target_types import (
    ImageArtifactExclusions,
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
    ImageEnvironment,
    ImageExtractMarker,
)
from pants_backend_oci.util_rules.assemble import (
    AssembleImageRequest,
    ConfigEdit,
    assemble_image,
    assembled_or_raise,
)
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.layer import ImageLayerRequest, build_image_layer
from pants_backend_oci.util_rules.run import ImageFilesRequest, image_files


@dataclass(frozen=True)
class ImageArtifactBuildFieldSet(PackageFieldSet):
    required_fields = (ImageBase, ImageBuildCommand, ImageBuildOutputs)

    base: ImageBase

    commands: ImageBuildCommand
    outputs: ImageBuildOutputs

    dependencies: ImageDependencies
    environment: ImageEnvironment

    output_path: OutputPathField

    exclude: ImageArtifactExclusions


@dataclass(frozen=True)
class ImageArtifactExtractFieldSet(PackageFieldSet):
    required_fields = (ImageBase, ImageBuildOutputs, ImageExtractMarker)

    base: ImageBase
    outputs: ImageBuildOutputs
    output_path: OutputPathField
    exclude: ImageArtifactExclusions


class ImageArtifactBuildRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageArtifactBuildFieldSet


class ImageArtifactExtractRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageArtifactExtractFieldSet


@rule(desc="Build artifact in container", level=LogLevel.DEBUG)
async def build_image_artifact(
    request: ImageArtifactBuildRequest,
) -> ProcessResult:
    base = await resolve_unparsed_address_inputs(
        request.target.base.to_unparsed_address_inputs(), **implicitly()
    )

    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    build_request, dependencies = await concurrently(
        ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly()),
        resolve_targets(**implicitly(DependenciesRequest(request.target.dependencies))),
    )

    maybe_built_base, *layers = await concurrently(
        resolve_image_bundle(**implicitly({build_request.request: FallibleImageBundleRequest})),
        *(build_image_layer(ImageLayerRequest(dependency)) for dependency in dependencies),
    )

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    bundle = maybe_built_base.output

    if request.target.dependencies.value:
        config = ConfigEdit(env=tuple(request.target.environment.value or ()), history=True)
        image = await assemble_image(
            AssembleImageRequest(bundle, (*layers, config), f"Assemble OCI image: {request.target.address}"),
            **implicitly(),
        )
        bundle = assembled_or_raise(image, request.target.address)

    return await image_files(
        ImageFilesRequest(
            request.target.address,
            request.target.output_path.value_or_default(file_ending="tar.gz"),
            bundle,
            request.target.commands.value,
            tuple(request.target.outputs.value or ()),
            exclude_patterns=tuple(request.target.exclude.value or ()),
            description=f"Run build step for {request.target.address}",
        ),
        **implicitly(),
    )


@rule(desc="Extract artifact from container", level=LogLevel.DEBUG)
async def extract_image_artifact(
    request: ImageArtifactExtractRequest,
) -> ProcessResult:
    base = await resolve_unparsed_address_inputs(
        request.target.base.to_unparsed_address_inputs(), **implicitly()
    )

    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    build_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())
    maybe_built_base = await resolve_image_bundle(
        **implicitly({build_request.request: FallibleImageBundleRequest})
    )

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    return await image_files(
        ImageFilesRequest(
            request.target.address,
            request.target.output_path.value_or_default(file_ending="tar.gz"),
            maybe_built_base.output,
            None,
            tuple(request.target.outputs.value or ()),
            exclude_patterns=tuple(request.target.exclude.value or ()),
            description=f"Extract files for {request.target.address}",
        ),
        **implicitly(),
    )


def rules():
    return [
        *collect_rules(),
    ]
