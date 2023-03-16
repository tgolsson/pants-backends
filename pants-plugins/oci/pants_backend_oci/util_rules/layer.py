"""

"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.target_types import FileSourceField
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests, Snapshot
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import FieldSetsPerTarget, FieldSetsPerTargetRequest, SourcesField, Target

from pants_backend_oci.util_rules.archive import CreateDeterministicTar

logger = logging


@dataclass(frozen=True)
class ImageLayerRequest:
    target: Target


@dataclass(frozen=True)
class ImageLayer:
    address: Address
    digest: Digest

    layer_command: tuple[str]
    config_command: tuple[str]


@rule
async def build_image_layer(request: ImageLayerRequest) -> ImageLayer:
    # Get all dependencies for the root target.
    root_dependencies = [request.target]

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

    embedded_pkgs_digest = [built_package.digest for built_package in embedded_pkgs]
    all_digests = (sources.snapshot.digest, *embedded_pkgs_digest)

    snapshot = await Get(
        Snapshot,
        MergeDigests(all_digests),
    )

    raw_layer_digest = await Get(Digest, CreateDeterministicTar(snapshot, "layers/image_bundle.tar"))

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
            "layers/image_bundle.tar",
        ),
        (
            "config",
            *config,
            "--image",
            "build:build",
        ),
    )


def rules():
    return collect_rules()
