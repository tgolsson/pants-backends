"""

"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests, Snapshot
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import FieldSetsPerTarget, FieldSetsPerTargetRequest, Target

from pants_backend_oci.util_rules.archive import CreateDeterministicTar


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
    embedded_pkgs_per_target = await Get(
        FieldSetsPerTarget,
        FieldSetsPerTargetRequest(PackageFieldSet, [request.target]),
    )

    # Package binary dependencies for build context.
    embedded_pkgs = await MultiGet(
        Get(BuiltPackage, PackageFieldSet, field_set) for field_set in embedded_pkgs_per_target.field_sets
    )

    embedded_pkgs_digest = [built_package.digest for built_package in embedded_pkgs]
    all_digests = embedded_pkgs_digest

    layer_digest = await Get(
        Digest,
        MergeDigests(all_digests),
    )

    snapshot = await Get(Snapshot, Digest, layer_digest)

    raw_layer_digest = await Get(Digest, CreateDeterministicTar(snapshot, "layers/image_bundle.tar"))

    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"
    config = [
        "--config.env",
        "BUILT_BY=pants.oci",
        "--config.entrypoint",
        f"/{embedded_pkgs[0].artifacts[0].relpath}",
        "--author=pants_backend_oci",
        f"--created={timestamp}",
        "--no-history",
    ]

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
