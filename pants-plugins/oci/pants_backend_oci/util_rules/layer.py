""" """

from __future__ import annotations

import logging
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackageArtifact, PackageFieldSet, build_package
from pants.core.target_types import FileSourceField
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import (
    CreateDigest,
    Digest,
    FileContent,
    FileEntry,
    MergeDigests,
    Snapshot,
    SymlinkEntry,
)
from pants.engine.internals.graph import find_valid_field_sets, resolve_targets
from pants.engine.intrinsics import (
    create_digest,
    digest_to_snapshot,
    get_digest_entries,
    merge_digests,
)
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSetsPerTargetRequest,
    SourcesField,
    Target,
)
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciSubsystem
from pants_backend_oci.target_types import (
    CaCertsPackagesField,
    DebLayerPackagesField,
    GroupEntriesField,
    LocalePackagesField,
    PasswdUsersField,
)
from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.sandboxed import OciBuilder


class BuiltLayerArtifact(BuiltPackageArtifact):
    pass


logger = logging

_FILE_LIST = "__pants_archive_filelist__"

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


@dataclass(frozen=True)
class FilesTarRequest:
    """A deterministic tar at `output_filename` of every file in `snapshot`."""

    snapshot: Snapshot
    output_filename: str


@rule(desc="Creating a layer tar", level=LogLevel.DEBUG)
async def create_files_tar(request: FilesTarRequest, builder: OciBuilder, oci: OciSubsystem) -> Digest:
    if oci.unsafe_tar_ignore_file_changed:
        logger.warning("`[oci].unsafe_tar_ignore_file_changed` is deprecated and has no effect.")
    # The paths go in a file, as a long argument list can exceed the OS limit. Modes come from the
    # executable bit, as a large file's sandbox mode is that of its hardlinked store copy.
    entries = await get_digest_entries(request.snapshot.digest)
    lines = [
        f"{'x' if isinstance(entry, FileEntry) and entry.is_executable else '-'}\t{entry.path}"
        for entry in entries
        if isinstance(entry, (FileEntry, SymlinkEntry))
    ]
    file_list = FileContent(_FILE_LIST, "\n".join(lines).encode("utf-8"))
    file_list_digest = await create_digest(CreateDigest([file_list]))
    input_digest = await merge_digests(MergeDigests([file_list_digest, request.snapshot.digest]))
    process = builder.process(
        oci_layout_builder,
        "tar",
        request.output_filename,
        "--files-from",
        _FILE_LIST,
        input_digest=input_digest,
        output_files=(request.output_filename,),
        description=f"Create {request.output_filename}",
        level=LogLevel.DEBUG,
    )
    result = await fallible_to_exec_result_or_raise(**implicitly({process: Process}))
    return result.output_digest


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

        layer_name = "layers/image_bundle.tar"
        raw_layer_digest = await create_files_tar(FilesTarRequest(snapshot, layer_name), **implicitly())
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
