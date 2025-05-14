""" """

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.core.target_types import FileSourceField
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests, Snapshot
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    SourcesField,
    Target,
    Targets,
)

from pants_backend_oci.util_rules.archive import CreateDeterministicTar


class BuiltLayerArtifact(BuiltPackageArtifact):
    pass


logger = logging


@dataclass(frozen=True)
class ImageLayerRequest:
    target: Target
    old_style: bool = True


@dataclass(frozen=True)
class ImageLayer:
    address: Address
    digest: Digest

    layer_command: tuple[str]
    config_command: tuple[str]

    compressed: bool = True


@rule
async def build_image_layer(request: ImageLayerRequest) -> ImageLayer:
    if request.old_style:
        root_dependencies = [request.target]

    else:
        if request.target.has_field(Dependencies):
            root_dependencies = await Get(Targets, DependenciesRequest(request.target[Dependencies]))
        else:
            root_dependencies = []

    # Get all file sources from the root dependencies. That includes any non-file sources that can
    # be "codegen"ed into a file source.
    sources_request = Get(
        SourceFiles,
        SourceFilesRequest(
            sources_fields=[tgt.get(SourcesField) for tgt in root_dependencies],
            for_sources_types=(FileSourceField,),
            enable_codegen=True,
        ),
    )

    embedded_pkgs_per_target_request = Get(
        FieldSetsPerTarget,
        FieldSetsPerTargetRequest(PackageFieldSet, root_dependencies),
    )

    sources, embedded_pkgs_per_target = await MultiGet(
        sources_request,
        embedded_pkgs_per_target_request,
    )

    sources_str = ", ".join(p for p in sources.files)
    if sources_str:
        logger.info(f"Built files for OCI image: {sources_str}")
    else:
        logger.info("Did not build any files for OCI image")

    # Package binary dependencies for build context.
    embedded_pkgs = await MultiGet(
        Get(BuiltPackage, PackageFieldSet, field_set) for field_set in embedded_pkgs_per_target.field_sets
    )

    packages_str = ", ".join(a.relpath for p in embedded_pkgs for a in p.artifacts if a.relpath)
    if packages_str:
        logger.info(f"Built {len(embedded_pkgs)} packages for OCI image: {packages_str}")
    else:
        logger.info("Did not build any packages for OCI image")
    other_artifacts = []
    layer_artifacts = []

    is_compressed = False
    for target in embedded_pkgs:
        if len(target.artifacts) > 1 or not isinstance(target.artifacts[0], BuiltLayerArtifact):
            other_artifacts.append(target)

        else:
            layer_artifacts.append(target)
            if target.artifacts[0].relpath.endswith(".tar.gz"):
                is_compressed = True

    embedded_pkgs_digest = [built_package.digest for built_package in other_artifacts]

    if sources.snapshot.files or sources.snapshot.dirs:
        all_digests = (sources.snapshot.digest, *embedded_pkgs_digest)
    else:
        all_digests = embedded_pkgs_digest

    real_layers = []

    if all_digests:
        snapshot = await Get(
            Snapshot,
            MergeDigests(all_digests),
        )

        raw_layer_digest = await Get(Digest, CreateDeterministicTar(snapshot, "layers/image_bundle.tar"))
        layer_name = "layers/image_bundle.tar"
        real_layers.append(
            (
                raw_layer_digest,
                layer_name,
            )
        )

    for layer in layer_artifacts:
        snapshot = await Get(
            Snapshot,
            MergeDigests([layer.digest]),
        )

        real_layers.append(
            (
                snapshot.digest,
                snapshot.files[0],
            )
        )

    if len(real_layers) > 1:
        # TODO[TSolberg]: Support multiple layers. We need to merge the layers together to create a single
        # layer that can be added to the image.
        #
        # Things to note:
        # Layer order matters. Determinism. `tar --concatenate` is ridiculusly slow.
        raise Exception(f"Multiple layers not yet supported: {real_layers}")

    raw_layer_digest, layer_name = real_layers[0]
    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"
    config = [
        "--config.env",
        "BUILT_BY=pants.oci",
        "--author=pants_backend_oci",
        f"--created={timestamp}",
        "--no-history",
    ]

    if embedded_pkgs:
        logger.info(f"Setting entrypoint to: {embedded_pkgs[0].artifacts[0].relpath}")
        config.extend(
            [
                "--config.entrypoint",
                f"/{embedded_pkgs[0].artifacts[0].relpath}",
            ]
        )

    return ImageLayer(
        request.target.address,
        raw_layer_digest,
        (
            "raw",
            "add-layer",
            "--history.author=pants_backend_oci",
            f"--history.created_by='Layer target: {request.target.address}'",
            f"--history.comment='Layer target: {request.target.address}'",
            f"--history.created={timestamp}",
            "--image",
            "build:build",
            layer_name,
        ),
        (
            "config",
            *config,
            "--image",
            "build:build",
        ),
        compressed=is_compressed,
    )


def rules():
    return collect_rules()
