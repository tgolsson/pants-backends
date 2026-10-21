# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.core.target_types import FileSourceField
from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import CreateDigest, FileContent, MergeDigests
from pants.engine.internals.graph import resolve_targets, transitive_targets
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    DependenciesRequest,
    InvalidFieldException,
    SourcesField,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.target_types import DebLayerMergedUsrField, DebLayerPackagesField
from pants_backend_oci.util_rules import deb_layer_builder
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact

_BUILDER_SOURCE = f"{deb_layer_builder.__name__.rpartition('.')[2]}.py"
_BUILDER_MODULE = "__deb_layer_builder"
_BUILDER_MAIN = f"import sys, {_BUILDER_MODULE}; sys.exit({_BUILDER_MODULE}.main(sys.argv[1:]))"


@dataclass(frozen=True)
class DebLayerPackageFieldSet(PackageFieldSet):
    required_fields = (DebLayerPackagesField,)

    debs: DebLayerPackagesField
    merged_usr: DebLayerMergedUsrField
    output_path: OutputPathField


@rule(desc="Package OCI layer from .deb packages", level=LogLevel.DEBUG)
async def package_deb_layer(
    field_set: DebLayerPackageFieldSet,
    python: PythonBuildStandaloneBinary,
) -> BuiltPackage:
    # The closure of `debs`, so a target that depends on the packages it needs (like those generated
    # from a Debian lockfile) brings them along. Targets without sources only group others.
    direct = await resolve_targets(**implicitly(DependenciesRequest(field_set.debs)))
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
            f"The `{DebLayerPackagesField.alias}` field of {field_set.address} must only contain "
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
            f"The `{DebLayerPackagesField.alias}` field of {field_set.address} must only contain "
            f"`.deb` files, got: {', '.join(not_debs) or 'nothing'}"
        )

    # Read through `importlib.resources` so this also works when the plugin is imported from a zip.
    builder_source = resources.files(__package__).joinpath(_BUILDER_SOURCE).read_bytes()
    script = await create_digest(CreateDigest([FileContent(f"{_BUILDER_MODULE}.py", builder_source)]))
    input_digest = await merge_digests(MergeDigests([script, sources.snapshot.digest]))

    relpath = field_set.output_path.value_or_default(file_ending="tar")
    argv = [python.path, "-c", _BUILDER_MAIN, "--output", relpath]
    if not field_set.merged_usr.value:
        argv.append("--no-merged-usr")

    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(*argv, *sources.files),
                input_digest=input_digest,
                output_files=(relpath,),
                description=f"Build OCI layer from .deb packages: {field_set.address}",
                level=LogLevel.DEBUG,
            )
        )
    )

    return BuiltPackage(result.output_digest, (BuiltLayerArtifact(relpath=relpath),))


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, DebLayerPackageFieldSet),
    ]
