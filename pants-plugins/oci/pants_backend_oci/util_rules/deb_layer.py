# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.core.target_types import FileSourceField
from pants.core.util_rules.external_tool import download_external_tool
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import MergeDigests, Snapshot
from pants.engine.internals.graph import resolve_targets, transitive_targets
from pants.engine.intrinsics import merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    InvalidFieldException,
    SourcesField,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import ZstdTool
from pants_backend_oci.target_types import DebLayerMergedUsrField, DebLayerPackagesField
from pants_backend_oci.util_rules import deb_layer_builder
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact
from pants_backend_oci.util_rules.sandboxed import OciBuilder


@dataclass(frozen=True)
class DebLayerPackageFieldSet(PackageFieldSet):
    required_fields = (DebLayerPackagesField,)

    debs: DebLayerPackagesField
    merged_usr: DebLayerMergedUsrField
    output_path: OutputPathField


@dataclass(frozen=True)
class DebFilesRequest:
    field: Dependencies
    address: Address


@dataclass(frozen=True)
class DebFiles:
    snapshot: Snapshot


@rule(desc="Resolve .deb packages", level=LogLevel.DEBUG)
async def resolve_deb_files(request: DebFilesRequest) -> DebFiles:
    # The closure of the field, so a target that depends on the packages it needs (like those
    # generated from a Debian lockfile) brings them along. Targets without sources only group others.
    direct = await resolve_targets(**implicitly(DependenciesRequest(request.field)))
    closure = await transitive_targets(
        TransitiveTargetsRequest([tgt.address for tgt in direct]), **implicitly()
    )
    not_files = [
        str(tgt.address)
        for tgt in closure.closure
        if tgt.has_field(SourcesField) and not tgt.has_field(FileSourceField)
    ]
    if not_files:
        raise InvalidFieldException(
            f"The `{request.field.alias}` field of {request.address} must only contain "
            f"`file` targets, got: {', '.join(not_files)}"
        )

    sources = await determine_source_files(
        SourceFilesRequest(
            sources_fields=[tgt[SourcesField] for tgt in closure.closure if tgt.has_field(SourcesField)],
            for_sources_types=(FileSourceField,),
            enable_codegen=True,
        )
    )

    not_debs = [f for f in sources.files if not f.endswith(".deb")]
    if not_debs or not sources.files:
        raise InvalidFieldException(
            f"The `{request.field.alias}` field of {request.address} must only contain "
            f"`.deb` files, got: {', '.join(not_debs) or 'nothing'}"
        )

    return DebFiles(sources.snapshot)


@rule(desc="Package OCI layer from .deb packages", level=LogLevel.DEBUG)
async def package_deb_layer(
    field_set: DebLayerPackageFieldSet,
    builder: OciBuilder,
    zstd_tool: ZstdTool,
    platform: Platform,
) -> BuiltPackage:
    debs = await resolve_deb_files(DebFilesRequest(field_set.debs, field_set.address))
    sources = debs.snapshot
    zstd = await download_external_tool(zstd_tool.get_request(platform))

    input_digest = await merge_digests(MergeDigests([sources.digest, zstd.digest]))

    relpath = field_set.output_path.value_or_default(file_ending="tar")
    args = ["--output", relpath, "--zstd", zstd.exe]
    if not field_set.merged_usr.value:
        args.append("--no-merged-usr")

    process = builder.process(
        deb_layer_builder,
        *args,
        *sources.files,
        input_digest=input_digest,
        output_files=(relpath,),
        description=f"Build OCI layer from .deb packages: {field_set.address}",
        level=LogLevel.DEBUG,
    )
    result = await fallible_to_exec_result_or_raise(**implicitly({process: Process}))

    return BuiltPackage(result.output_digest, (BuiltLayerArtifact(relpath=relpath),))


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, DebLayerPackageFieldSet),
    ]
