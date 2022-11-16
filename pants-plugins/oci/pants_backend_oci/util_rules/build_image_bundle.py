from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.util_rules.archive import CreateArchive
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import Digest, MergeDigests, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSet,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    Target,
    Targets,
    WrappedTarget,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants_backend_oci.subsystem import UmociTool
from pants_backend_oci.target_types import ImageBase
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundle,
    ImageBundleRequest,
)


@dataclass(frozen=True)
class BuildImageBundleFieldSet(FieldSet):
    required_fields = (ImageBase,)

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

    build_request = await Get(
        FallibleImageBundleRequestWrap, ImageBundleRequest(wrapped_target.target)
    )
    maybe_built_base = await Get(
        FallibleImageBundle, FallibleImageBundleRequest, build_request.request
    )

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, umoci.get_request(platform))
    base = maybe_built_base.output
    base_digest = base.digest

    if request.target.dependencies:
        root_dependencies = await Get(Targets, DependenciesRequest(request.target.dependencies))

        embedded_pkgs_per_target = await Get(
            FieldSetsPerTarget,
            FieldSetsPerTargetRequest(PackageFieldSet, root_dependencies),
        )

        # Package binary dependencies for build context.
        embedded_pkgs = await MultiGet(
            Get(BuiltPackage, PackageFieldSet, field_set)
            for field_set in embedded_pkgs_per_target.field_sets
        )

        embedded_pkgs_digest = [built_package.digest for built_package in embedded_pkgs]
        all_digests = embedded_pkgs_digest

        layer_digest = await Get(
            Digest,
            MergeDigests(all_digests),
        )

        snapshot = await Get(Snapshot, Digest, layer_digest)

        raw_layer_digest = await Get(
            Digest, CreateArchive(snapshot, "layers/image_bundle.tar", "tar")
        )

        command_digest = await Get(
            Digest,
            MergeDigests([raw_layer_digest, umoci.digest, base_digest]),
        )

        config = [
            "--config.env",
            "BUILT_BY=pants.oci",
            "--config.entrypoint",
            f"/{embedded_pkgs[0].artifacts[0].relpath}",
        ]

        compile_result = await Get(
            FallibleProcessResult,
            FusedProcess(
                (
                    Process(
                        (
                            umoci.exe,
                            "raw",
                            "add-layer",
                            "--image",
                            "build:build",
                            "layers/image_bundle.tar",
                        ),
                        input_digest=command_digest,
                        description=f"Package OCI Image Bundle: {request.target.address}",
                    ),
                    Process(
                        (
                            umoci.exe,
                            "config",
                            *config,
                            "--image",
                            "build:build",
                        ),
                        input_digest=command_digest,
                        description=f"Configure OCI Image: {request.target.address}",
                        output_directories=("build",),
                    ),
                ),
            ),
        )

        if compile_result.exit_code != 0:
            return FallibleImageBundle(
                None,
                compile_result.exit_code,
                stdout=compile_result.stdout.decode("utf-8"),
                stderr=compile_result.stderr.decode("utf-8"),
            )

        compilation_digest = compile_result.output_digest

        output_digest = compilation_digest

    else:
        output_digest = base_digest

    output = ImageBundle(
        digest=output_digest,
    )

    return FallibleImageBundle(output)


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, BuildImageBundleRequest),
    ]
