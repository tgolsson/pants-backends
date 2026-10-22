from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from pants.engine.internals.graph import (
    resolve_target,
    resolve_targets,
    resolve_unparsed_address_inputs,
)
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import DependenciesRequest, FieldSet, Target, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.target_types import (
    ImageArgs,
    ImageBase,
    ImageBuildCommand,
    ImageDependencies,
    ImageEntrypoint,
    ImageEnvironment,
    ImageLayersField,
    ImageUser,
)
from pants_backend_oci.util_rules.assemble import AssembleImageRequest, ConfigEdit, assemble_image
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.layer import ImageLayerRequest, build_image_layer
from pants_backend_oci.util_rules.run import RunContainerRequest, run_in_container


@dataclass(frozen=True)
class BuildImageBundleFieldSet(FieldSet):
    required_fields = (ImageBase, ImageDependencies)

    base: ImageBase
    dependencies: ImageDependencies
    environment: ImageEnvironment
    entrypoint: ImageEntrypoint
    args: ImageArgs
    user: ImageUser
    layers: ImageLayersField

    commands: ImageBuildCommand


@dataclass(frozen=True)
class BuildImageBundleRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type = BuildImageBundleFieldSet


@rule(desc="Build OCI image", level=LogLevel.DEBUG)
async def build_oci_bundle_package(request: BuildImageBundleRequest) -> FallibleImageBundle:
    base = await resolve_unparsed_address_inputs(
        request.target.base.to_unparsed_address_inputs(), **implicitly()
    )

    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    build_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())

    layer_requests = []
    if request.target.layers:
        layer_dependencies = await resolve_targets(**implicitly(DependenciesRequest(request.target.layers)))

        for dependency in layer_dependencies:
            layer_requests.append(build_image_layer(ImageLayerRequest(dependency, old_style=False)))

    if request.target.dependencies:
        root_dependencies = await resolve_targets(
            **implicitly(DependenciesRequest(request.target.dependencies))
        )

        for dependency in root_dependencies:
            layer_requests.append(build_image_layer(ImageLayerRequest(dependency)))

    maybe_built_base, *layers = await concurrently(
        resolve_image_bundle(**implicitly({build_request.request: FallibleImageBundleRequest})),
        *layer_requests,
    )

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    entrypoint = request.target.entrypoint.value
    config = ConfigEdit(
        env=tuple(request.target.environment.value or ()),
        cmd=tuple(request.target.args.value) if request.target.args.value else None,
        user=request.target.user.value or None,
        entrypoint=(entrypoint,) if entrypoint else None,
        clear=("config.entrypoint", "config.cmd") if entrypoint else (),
        history=True,
    )

    description = f"Assemble OCI image: {request.target.address}"
    if not request.target.commands.value:
        return await assemble_image(
            AssembleImageRequest(maybe_built_base.output.digest, (*layers, config), description),
            **implicitly(),
        )

    image = await assemble_image(
        AssembleImageRequest(maybe_built_base.output.digest, tuple(layers), description), **implicitly()
    )
    if image.output is None:
        return image

    modified_image = await run_in_container(
        RunContainerRequest(image.output, request.target.commands.value, True), **implicitly()
    )

    return await assemble_image(
        AssembleImageRequest(modified_image.output_digest, (config,), description), **implicitly()
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, BuildImageBundleRequest),
    ]
