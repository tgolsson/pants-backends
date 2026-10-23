""" """

from __future__ import annotations

import logging
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackageArtifact, PackageFieldSet, build_package
from pants.core.target_types import FileSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.graph import find_valid_field_sets, resolve_targets
from pants.engine.intrinsics import digest_to_snapshot
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSetsPerTargetRequest,
    SourcesField,
    Target,
)

from pants_backend_oci.target_types import (
    CaCertsPackagesField,
    DebLayerPackagesField,
    GroupEntriesField,
    LocalePackagesField,
    PasswdUsersField,
)
from pants_backend_oci.util_rules.archive import CreateDeterministicTar, create_archive


class BuiltLayerArtifact(BuiltPackageArtifact):
    pass


logger = logging

# Targets that package into a layer themselves, rather than having their dependencies put in one.
SELF_LAYER_FIELDS = (
    DebLayerPackagesField,
    PasswdUsersField,
    GroupEntriesField,
    CaCertsPackagesField,
    LocalePackagesField,
)


@dataclass(frozen=True)
class ImageLayerRequest:
    target: Target
    old_style: bool = True


@dataclass(frozen=True)
class ImageLayer:
    address: Address
    digest: Digest

    # The layer tar, plain or gzipped, within `digest`.
    path: str
    # The packaged artifact to run, when the layer brings one.
    entrypoint: str | None = None
    # What made the layer, for its history; the target's address if unset.
    created_by: str | None = None


@rule
async def build_image_layer(request: ImageLayerRequest) -> ImageLayer:
    if request.old_style or any(request.target.has_field(f) for f in SELF_LAYER_FIELDS):
        root_dependencies = [request.target]

    else:
        if request.target.has_field(Dependencies):
            root_dependencies = await resolve_targets(
                **implicitly(DependenciesRequest(request.target[Dependencies]))
            )
        else:
            root_dependencies = []

    # Get all file sources from the root dependencies. That includes any non-file sources that can
    # be "codegen"ed into a file source.
    sources_request = determine_source_files(
        SourceFilesRequest(
            sources_fields=[tgt.get(SourcesField) for tgt in root_dependencies],
            for_sources_types=(FileSourceField,),
            enable_codegen=True,
        )
    )

    embedded_pkgs_per_target_request = find_valid_field_sets(
        FieldSetsPerTargetRequest(PackageFieldSet, root_dependencies), **implicitly()
    )

    sources, embedded_pkgs_per_target = await concurrently(
        sources_request,
        embedded_pkgs_per_target_request,
    )

    sources_str = ", ".join(p for p in sources.files)
    if sources_str:
        logger.info(f"Built files for OCI image: {sources_str}")
    else:
        logger.info("Did not build any files for OCI image")

    # Package binary dependencies for build context.
    embedded_pkgs = await concurrently(
        build_package(**implicitly({field_set: PackageFieldSet}))
        for field_set in embedded_pkgs_per_target.field_sets
    )

    packages_str = ", ".join(a.relpath for p in embedded_pkgs for a in p.artifacts if a.relpath)
    if packages_str:
        logger.info(f"Built {len(embedded_pkgs)} packages for OCI image: {packages_str}")
    else:
        logger.info("Did not build any packages for OCI image")
    other_artifacts = []
    layer_artifacts = []

    for target in embedded_pkgs:
        if len(target.artifacts) > 1 or not isinstance(target.artifacts[0], BuiltLayerArtifact):
            other_artifacts.append(target)

        else:
            layer_artifacts.append(target)

    embedded_pkgs_digest = [built_package.digest for built_package in other_artifacts]

    if sources.snapshot.files or sources.snapshot.dirs:
        all_digests = (sources.snapshot.digest, *embedded_pkgs_digest)
    else:
        all_digests = embedded_pkgs_digest

    real_layers = []

    if all_digests:
        snapshot = await digest_to_snapshot(**implicitly(MergeDigests(all_digests)))

        raw_layer_digest = await create_archive(
            CreateDeterministicTar(snapshot, "layers/image_bundle.tar"), **implicitly()
        )
        layer_name = "layers/image_bundle.tar"
        real_layers.append(
            (
                raw_layer_digest,
                layer_name,
            )
        )

    for layer in layer_artifacts:
        snapshot = await digest_to_snapshot(**implicitly(MergeDigests([layer.digest])))

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
    entrypoint = None
    if other_artifacts:
        entrypoint = f"/{other_artifacts[0].artifacts[0].relpath}"
        logger.info(f"Setting entrypoint to: {entrypoint}")

    return ImageLayer(request.target.address, raw_layer_digest, layer_name, entrypoint)


def rules():
    return collect_rules()
