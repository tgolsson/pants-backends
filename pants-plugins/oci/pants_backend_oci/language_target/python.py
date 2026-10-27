import dataclasses
from dataclasses import dataclass

from pants.engine.internals.graph import (
    resolve_target,
    resolve_targets,
    resolve_unparsed_address_inputs,
)
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    DependenciesRequest,
    FieldSet,
    StringField,
    Target,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.util.strutil import softwrap

from pants_backend_oci.target_types import ImageBase, ImageRepository, ImageTag
from pants_backend_oci.util_rules.assemble import AssembleImageRequest, ConfigEdit, assemble_image
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.layer import ImageLayerRequest, build_image_layer


class PythonMain(StringField):
    alias = "main"
    help = softwrap(""" The python main to use. If not provided, the rule will
        attempt to derive it from the dependencies.  """)


class PythonImageLayers(Dependencies):
    alias = "packages"
    help = softwrap("""The dependencies to add to the image""")


class PythonImageBuild(Target):
    alias = "oci_python_image"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        ImageTag,
        ImageBase,
        PythonImageLayers,
        PythonMain,
    )
    help = "An OCI image specialized for Python."


@dataclass(frozen=True)
class BuildPythonImageFieldSet(FieldSet):
    required_fields = (ImageBase, PythonMain, PythonImageLayers)

    base: ImageBase
    python_main: PythonMain
    dependencies: PythonImageLayers


@dataclass(frozen=True)
class BuildPythonImageRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type = BuildPythonImageFieldSet


@rule(desc="Build Python OCI image")
async def build_python_image(request: BuildPythonImageRequest) -> FallibleImageBundle:
    base = await resolve_unparsed_address_inputs(
        request.target.base.to_unparsed_address_inputs(), **implicitly()
    )
    wrapped_target = await resolve_target(
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"), **implicitly()
    )

    target = wrapped_target.target
    bundle_request, dependencies = await concurrently(
        ibr_to_fibr(ImageBundleRequest(target), **implicitly()),
        resolve_targets(**implicitly(DependenciesRequest(request.target.dependencies))),
    )

    base_image, *layers = await concurrently(
        resolve_image_bundle(**implicitly({bundle_request.request: FallibleImageBundleRequest})),
        *(build_image_layer(ImageLayerRequest(dependency)) for dependency in dependencies),
    )
    if base_image.output is None:
        return dataclasses.replace(base_image, dependency_failed=True)

    pex = request.target.python_main.value
    if pex is None:
        pex = next((layer.entrypoint for layer in layers if (layer.entrypoint or "").endswith(".pex")), None)

    ops: list = list(layers)
    if pex is not None:
        ops.append(ConfigEdit(entrypoint=("python", pex), history=True))

    return await assemble_image(
        AssembleImageRequest(
            base_image.output, tuple(ops), f"Assemble Python OCI image: {request.target.address}"
        ),
        **implicitly(),
    )


def rules():
    return [
        UnionRule(FallibleImageBundleRequest, BuildPythonImageRequest),
        *collect_rules(),
    ]
