""" """

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import ClassVar

from pants.core.goals.package import OutputPathField, PackageFieldSet
from pants.core.util_rules.external_tool import download_external_tool
from pants.core.util_rules.system_binaries import BashBinary, CatBinary, CpBinary, MkdirBinary
from pants.engine.fs import MergeDigests
from pants.engine.internals.graph import (
    resolve_target,
    resolve_targets,
    resolve_unparsed_address_inputs,
)
from pants.engine.intrinsics import merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult, fallible_to_exec_result_or_raise
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import DependenciesRequest, FieldSet, Target, WrappedTargetRequest
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import RuncTool, UmociTool
from pants_backend_oci.target_types import (
    ImageArtifactExclusions,
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
    ImageEnvironment,
    ImageExtractMarker,
)
from pants_backend_oci.util_rules.copy import CopyFromRequest, copy_from_container
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
    ImageBundleRequest,
    ibr_to_fibr,
)
from pants_backend_oci.util_rules.layer import ImageLayerRequest, build_image_layer
from pants_backend_oci.util_rules.run import RunContainerRequest, run_in_container


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
    umoci: UmociTool,
    runc: RuncTool,
    platform: Platform,
    bash: BashBinary,
    cat: CatBinary,
    cp: CpBinary,
    mkdir: MkdirBinary,
) -> ProcessResult:
    base = await resolve_unparsed_address_inputs(request.target.base.to_unparsed_address_inputs())

    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    build_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())
    maybe_built_base = await Get(FallibleImageBundle, FallibleImageBundleRequest, build_request.request)

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    umoci_request = download_external_tool(umoci.get_request(platform))
    base = maybe_built_base.output
    base_digest = base.digest

    if request.target.dependencies.value:
        dependencies = await concurrently(
            resolve_targets(**implicitly(DependenciesRequest(request.target.dependencies))),
        )

        layers = []
        for dependency in dependencies:
            layers.append(build_image_layer(ImageLayerRequest(dependency[0])))

        umoci, *layers = await concurrently(
            umoci_request,
            *layers,
        )

        for layer in layers:
            input_digest = await merge_digests(MergeDigests([umoci.digest, base_digest, layer.digest]))

            image_with_layer = await fallible_to_exec_result_or_raise(
                **implicitly(
                    Process(
                        (umoci.exe, *layer.layer_command),
                        input_digest=input_digest,
                        description=f"Package OCI Layer Artifact: {layer.address}",
                        output_directories=("build/",),
                    )
                )
            )

            digest = image_with_layer.output_digest
            input_digest = await merge_digests(MergeDigests([umoci.digest, digest]))
            image_with_config = await fallible_to_exec_result_or_raise(
                **implicitly(
                    Process(
                        (umoci.exe, *layer.config_command),
                        input_digest=input_digest,
                        description=f"Configure OCI Layer Artifact: {layer.address}",
                        output_directories=("build/",),
                    )
                )
            )
            base_digest = image_with_config.output_digest

        config = []
        for value in request.target.environment.value:
            config.extend(
                [
                    "--config.env",
                    value,
                ]
            )

        output_digest = base_digest
        image_with_config = await fallible_to_exec_result_or_raise(
            **implicitly(
                Process(
                    (
                        umoci.exe,
                        "config",
                        *config,
                        "--image",
                        "build:build",
                    ),
                    input_digest=input_digest,
                    description=f"Configure OCI Image Bundle: {layer.address}",
                    output_directories=("build/",),
                )
            )
        )

        output_digest = image_with_config.output_digest
    else:
        output_digest = base_digest

    bundle = ImageBundle(output_digest, "", True)

    modified_image = await run_in_container(
        RunContainerRequest(bundle, request.target.commands.value, True), **implicitly()
    )
    bundle = ImageBundle(modified_image.output_digest, "", True)

    artifacts = await copy_from_container(
        CopyFromRequest(
            request.target.output_path.value_or_default(file_ending="tar.gz"),
            bundle,
            tuple(),
            request.target.outputs.value,
            exclude_patterns=tuple(request.target.exclude.value or ()),
        ),
        **implicitly(),
    )

    return artifacts


@rule(desc="Extract artifact from container", level=LogLevel.DEBUG)
async def extract_image_artifact(
    request: ImageArtifactExtractRequest,
    umoci: UmociTool,
    platform: Platform,
) -> ProcessResult:
    base = await resolve_unparsed_address_inputs(request.target.base.to_unparsed_address_inputs())

    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    build_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())
    maybe_built_base = await Get(FallibleImageBundle, FallibleImageBundleRequest, build_request.request)

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    base = maybe_built_base.output

    bundle = ImageBundle(base.digest, "", True)

    artifacts = await copy_from_container(
        CopyFromRequest(
            request.target.output_path.value_or_default(file_ending="tar.gz"),
            bundle,
            tuple(),
            request.target.outputs.value,
            exclude_patterns=tuple(request.target.exclude.value or ()),
        ),
        **implicitly(),
    )

    return artifacts


def rules():
    return [
        *collect_rules(),
    ]
