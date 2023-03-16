from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSet,
    Target,
    Targets,
    WrappedTarget,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import UmociTool
from pants_backend_oci.target_types import ImageBase, ImageDependencies
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundle,
    ImageBundleRequest,
)
from pants_backend_oci.util_rules.layer import ImageLayer, ImageLayerRequest
from pants_backend_oci.util_rules.oci_sha import OciSha, OciShaRequest


@dataclass(frozen=True)
class BuildImageBundleFieldSet(FieldSet):
    required_fields = (ImageBase, ImageDependencies)

    base: ImageBase
    dependencies: Dependencies


@dataclass(frozen=True)
class BuildImageBundleRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type = BuildImageBundleFieldSet


@rule(desc="Build OCI image", level=LogLevel.DEBUG)
async def build_oci_bundle_package(
    request: BuildImageBundleRequest,
    umoci: UmociTool,
    platform: Platform,
) -> FallibleImageBundle:
    base = await Get(
        Addresses,
        UnparsedAddressInputs,
        request.target.base.to_unparsed_address_inputs(),
    )

    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"),
    )

    build_request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(wrapped_target.target))
    maybe_built_base = await Get(FallibleImageBundle, FallibleImageBundleRequest, build_request.request)

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, umoci.get_request(platform))
    base = maybe_built_base.output
    base_digest = base.digest

    if request.target.dependencies:
        root_dependencies = await Get(Targets, DependenciesRequest(request.target.dependencies))

        layers = []
        for dependency in root_dependencies:
            layers.append(Get(ImageLayer, ImageLayerRequest(dependency)))

        layers = await MultiGet(*layers)

        for layer in layers:
            input_digest = await Get(Digest, MergeDigests([umoci.digest, base_digest, layer.digest]))

            image_with_layer = await Get(
                ProcessResult,
                FusedProcess(
                    (
                        Process(
                            (umoci.exe, *layer.layer_command),
                            input_digest=input_digest,
                            description=f"Package OCI Image Bundle: {layer.address}",
                            output_directories=("build/",),
                        ),
                        Process(
                            (umoci.exe, *layer.config_command),
                            input_digest=input_digest,
                            description=f"Configure OCI Image Bundle: {layer.address}",
                            output_directories=("build/",),
                        ),
                    )
                ),
            )
            base_digest = image_with_layer.output_digest

        output_digest = base_digest

    else:
        output_digest = base_digest

    image_digest = await Get(OciSha, OciShaRequest(output_digest))

    output = ImageBundle(digest=output_digest, image_sha=image_digest.image_digest, is_local=True)

    return FallibleImageBundle(output)


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, BuildImageBundleRequest),
    ]
