from __future__ import annotations

import dataclasses
import datetime
import os
from dataclasses import dataclass

from pants.core.util_rules.adhoc_binaries import GunzipBinary
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
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
from pants_backend_oci.target_types import (
    ImageArgs,
    ImageBase,
    ImageBuildCommand,
    ImageDependencies,
    ImageEntrypoint,
    ImageEnvironment,
    ImageLayersField,
)
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
from pants_backend_oci.util_rules.run import RunContainerRequest


@dataclass(frozen=True)
class BuildImageBundleFieldSet(FieldSet):
    required_fields = (ImageBase, ImageDependencies)

    base: ImageBase
    dependencies: ImageDependencies
    environment: ImageEnvironment
    entrypoint: ImageEntrypoint
    args: ImageArgs
    layers: ImageLayersField

    commands: ImageBuildCommand


@dataclass(frozen=True)
class BuildImageBundleRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type = BuildImageBundleFieldSet


@rule(desc="Build OCI image", level=LogLevel.DEBUG)
async def build_oci_bundle_package(
    request: BuildImageBundleRequest,
    umoci: UmociTool,
    platform: Platform,
    gunzip: GunzipBinary,
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

    build_request, umoci = await MultiGet(
        Get(FallibleImageBundleRequestWrap, ImageBundleRequest(wrapped_target.target)),
        Get(DownloadedExternalTool, ExternalToolRequest, umoci.get_request(platform)),
    )

    layer_requests = []
    if request.target.layers:
        layer_dependencies = await Get(Targets, DependenciesRequest(request.target.layers))

        for dependency in layer_dependencies:
            layer_requests.append(Get(ImageLayer, ImageLayerRequest(dependency, old_style=False)))

    if request.target.dependencies:
        root_dependencies = await Get(Targets, DependenciesRequest(request.target.dependencies))

        for dependency in root_dependencies:
            layer_requests.append(Get(ImageLayer, ImageLayerRequest(dependency)))

    maybe_built_base, *layers = await MultiGet(
        Get(FallibleImageBundle, FallibleImageBundleRequest, build_request.request),
        *layer_requests,
    )

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    base = maybe_built_base.output
    output_digest = base.digest

    for layer in layers:
        input_digest = await Get(Digest, MergeDigests([umoci.digest, output_digest, layer.digest]))

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
            FallibleProcessResult,
            FusedProcess(
                layer_processes,
            ),
        )

        if image.exit_code != 0:
            return FallibleImageBundle(
                None,
                image.exit_code,
                stdout=image.stdout.decode("utf-8"),
                stderr=image.stderr.decode("utf-8"),
            )

        output_digest = image.output_digest

    if request.target.commands.value:
        bundle = ImageBundle(output_digest, "", True)

        modified_image = await Get(
            ProcessResult, RunContainerRequest(bundle, request.target.commands.value, True)
        )

        output_digest = modified_image.output_digest

    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"
    config = [
        f"--history.created={timestamp}",
    ]
    if request.target.environment.value:
        for value in request.target.environment.value:
            config.extend(
                [
                    "--config.env",
                    value,
                ]
            )

    if request.target.args.value:
        for arg in request.target.args.value:
            config.extend(["--config.cmd", arg])

    if request.target.entrypoint.value:
        config.extend(["--clear=config.entrypoint"])
        config.extend(["--clear=config.cmd"])
        config.extend(["--config.entrypoint", request.target.entrypoint.value])

    if config:
        input_digest = await Get(Digest, MergeDigests([umoci.digest, output_digest]))

        compile_result = await Get(
            FallibleProcessResult,
            Process(
                argv=(
                    umoci.exe,
                    "config",
                    *config,
                    "--image",
                    "build:build",
                ),
                input_digest=input_digest,
                description="Configure OCI environment",
                output_directories=("build/",),
            ),
        )

        if compile_result.exit_code != 0:
            return FallibleImageBundle(
                None,
                compile_result.exit_code,
                stdout=compile_result.stdout.decode("utf-8"),
                stderr=compile_result.stderr.decode("utf-8"),
            )

        output_digest = compile_result.output_digest

    image_digest = await Get(OciSha, OciShaRequest(output_digest))
    output = ImageBundle(digest=output_digest, image_sha=image_digest.image_digest, is_local=True)

    return FallibleImageBundle(output)


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, BuildImageBundleRequest),
    ]
