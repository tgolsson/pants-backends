""" """

import datetime
import os
from dataclasses import dataclass

from pants.core.util_rules.adhoc_binaries import GunzipBinary
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    DependenciesRequest,
    FieldSet,
    StringField,
    Target,
    Targets,
    WrappedTarget,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.util.strutil import softwrap

from pants_backend_oci.subsystem import UmociTool
from pants_backend_oci.target_types import ImageBase, ImageRepository, ImageTag
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
async def build_python_image(
    request: BuildPythonImageRequest,
    umoci: UmociTool,
    platform: Platform,
    gunzip: GunzipBinary,
) -> FallibleImageBundle:
    umoci_request = Get(DownloadedExternalTool, ExternalToolRequest, umoci.get_request(platform))
    base = await Get(
        Addresses,
        UnparsedAddressInputs,
        request.target.base.to_unparsed_address_inputs(),
    )
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"),
    )

    target = wrapped_target.target
    bundle_request, dependencies = await MultiGet(
        Get(FallibleImageBundleRequestWrap, ImageBundleRequest(target)),
        Get(Targets, DependenciesRequest(request.target.dependencies)),
    )

    base_image = Get(FallibleImageBundle, FallibleImageBundleRequest, bundle_request.request)

    layers = []
    for dependency in dependencies:
        layers.append(Get(ImageLayer, ImageLayerRequest(dependency)))

    base_image, umoci, *layers = await MultiGet(
        base_image,
        umoci_request,
        *layers,
    )

    pex = request.target.python_main.value
    digest = base_image.output.digest

    for layer in layers:
        if pex is None:
            if "--config.entrypoint" in layer.config_command:
                idx = layer.config_command.index("--config.entrypoint")
                ep = layer.config_command[idx + 1]
                if ep.endswith(".pex"):
                    pex = ep

        input_digest = await Get(Digest, MergeDigests([umoci.digest, digest, layer.digest]))
        layer_processes = (
            Process(
                (umoci.exe, *layer.layer_command[:-1], layer.layer_command[-1].rstrip(".gz")),
                input_digest=input_digest,
                description=f"Package OCI Image Bundle: {layer.address}",
            ),
            Process(
                (umoci.exe, *layer.config_command),
                description=f"Configure OCI Image Bundle: {layer.address}",
                output_directories=("build/",),
            ),
        )
        if layer.compressed:
            layer_processes = (
                Process(
                    argv=gunzip.extract_archive_argv(
                        layer.layer_command[-1], os.path.dirname(layer.layer_command[-1])
                    ),
                    description=f"Uncompress OCI Image Bundle: {layer.address}",
                ),
                *layer_processes,
            )
        image = await Get(
            ProcessResult,
            FusedProcess(
                layer_processes,
            ),
        )

        digest = image.output_digest

    input_digest = await Get(Digest, MergeDigests([umoci.digest, digest]))
    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"

    if pex is not None:
        image_with_layer = await Get(
            ProcessResult,
            Process(
                (
                    umoci.exe,
                    "config",
                    "--image",
                    "build:build",
                    "--config.entrypoint",
                    "python",
                    "--config.entrypoint",
                    pex,
                    f"--history.created={timestamp}",
                ),
                input_digest=input_digest,
                description=f"Package OCI Image Bundle: {layer.address}",
                output_directories=("build/",),
            ),
        )
    else:
        image_with_layer = image

    image_digest = await Get(OciSha, OciShaRequest(image_with_layer.output_digest))
    output = ImageBundle(
        digest=image_with_layer.output_digest,
        image_sha=image_digest.image_digest,
        is_local=True,
    )
    return FallibleImageBundle(output)


def rules():
    return [
        UnionRule(FallibleImageBundleRequest, BuildPythonImageRequest),
        *collect_rules(),
    ]
