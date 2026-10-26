from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from pants.engine.addresses import Address, UnparsedAddressInputs
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
from pants_backend_oci.util_rules.run import ScriptedLayerRequest, run_scripted_layer


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


@dataclass(frozen=True)
class ImageLayersRequest:
    """The base and layers of an `oci_image_build`, in the order they're stacked."""

    field_set: BuildImageBundleFieldSet


@dataclass(frozen=True)
class ImageLayerRequests:
    base: FallibleImageBundleRequest
    layers: tuple[ImageLayerRequest, ...]


@rule
async def image_layer_requests(request: ImageLayersRequest) -> ImageLayerRequests:
    base = await resolve_unparsed_address_inputs(
        request.field_set.base.to_unparsed_address_inputs(), **implicitly()
    )

    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    build_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())

    layer_requests = []
    if request.field_set.layers:
        layers = request.field_set.layers
        layer_dependencies, declared = await concurrently(
            resolve_targets(**implicitly(DependenciesRequest(layers))),
            resolve_unparsed_address_inputs(
                UnparsedAddressInputs(
                    (v for v in layers.value or () if not v.startswith("!")),
                    owning_address=layers.address,
                    description_of_origin=f"the `{layers.alias}` field of {layers.address}",
                    # `DependenciesRequest` validates them, filling in parameters first.
                    skip_invalid_addresses=True,
                ),
                **implicitly(),
            ),
        )

        # Dependencies come back sorted by address; stack them in the order they're declared,
        # matching parameter-filled addresses by their unparametrized one. Anything still
        # unmatched keeps its sorted place at the end.
        position: dict[Address, int] = {}
        for i, address in enumerate(declared):
            position.setdefault(address, i)
            position.setdefault(address.parametrize({}, replace=True), i)

        def declared_position(target: Target) -> int:
            address = target.address
            return position.get(address, position.get(address.parametrize({}, replace=True), len(declared)))

        for dependency in sorted(layer_dependencies, key=declared_position):
            layer_requests.append(ImageLayerRequest(dependency, old_style=False))

    if request.field_set.dependencies:
        root_dependencies = await resolve_targets(
            **implicitly(DependenciesRequest(request.field_set.dependencies))
        )

        for dependency in root_dependencies:
            layer_requests.append(ImageLayerRequest(dependency))

    return ImageLayerRequests(build_request.request, tuple(layer_requests))


@rule(desc="Build OCI image", level=LogLevel.DEBUG)
async def build_oci_bundle_package(request: BuildImageBundleRequest) -> FallibleImageBundle:
    requests = await image_layer_requests(ImageLayersRequest(request.target), **implicitly())

    maybe_built_base, *layers = await concurrently(
        resolve_image_bundle(**implicitly({requests.base: FallibleImageBundleRequest})),
        *(build_image_layer(layer_request) for layer_request in requests.layers),
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

    scripted = await run_scripted_layer(
        ScriptedLayerRequest(image.output, request.target.commands.value, request.target.address),
        **implicitly(),
    )

    return await assemble_image(
        AssembleImageRequest(image.output.digest, (scripted, config), description), **implicitly()
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, BuildImageBundleRequest),
    ]
