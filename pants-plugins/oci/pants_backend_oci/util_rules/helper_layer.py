# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Rules for the declarative helper layers: `oci_passwd_layer`, `oci_group_layer`,
`oci_cacerts_layer` and `oci_locale_layer`. Every layer is built by `helper_layer_builder` in a
sandboxed process; the CA bundle and locale layers also get a `zstd` binary for zstd-compressed
debs."""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.addresses import Address
from pants.engine.fs import CreateDigest, Digest, DigestSubset, FileContent, MergeDigests, PathGlobs
from pants.engine.intrinsics import (
    create_digest,
    digest_subset_to_digest,
    execute_process,
    merge_digests,
)
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import InvalidFieldException
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import ZstdTool
from pants_backend_oci.target_types import (
    CaCertsPackagesField,
    GroupEntriesField,
    LocalePackagesField,
    LocalesField,
    PasswdUsersField,
)
from pants_backend_oci.util_rules import deb_layer_builder, helper_layer_builder
from pants_backend_oci.util_rules.deb_layer import DebFilesRequest, resolve_deb_files
from pants_backend_oci.util_rules.helper_layer_builder import (
    CA_CERTIFICATES,
    GroupEntry,
    PasswdEntry,
)
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules

# The builders are copied into the sandbox under their package path, so their imports still resolve.
_BUILDER_MODULES = (deb_layer_builder, helper_layer_builder)
_BUILDER_MAIN = module_main(helper_layer_builder)
_SPEC = "__helper_layer_spec.json"


@dataclass(frozen=True)
class PasswdLayerFieldSet(PackageFieldSet):
    required_fields = (PasswdUsersField,)

    users: PasswdUsersField
    output_path: OutputPathField


@dataclass(frozen=True)
class GroupLayerFieldSet(PackageFieldSet):
    required_fields = (GroupEntriesField,)

    groups: GroupEntriesField
    output_path: OutputPathField


@dataclass(frozen=True)
class CaCertsLayerFieldSet(PackageFieldSet):
    required_fields = (CaCertsPackagesField,)

    debs: CaCertsPackagesField
    output_path: OutputPathField


@dataclass(frozen=True)
class LocaleLayerFieldSet(PackageFieldSet):
    required_fields = (LocalePackagesField,)

    debs: LocalePackagesField
    locales: LocalesField
    output_path: OutputPathField


@dataclass(frozen=True)
class HelperLayerRequest:
    """Build a helper layer with `helper_layer_builder.main <layer> --output <path> <args>`."""

    layer: str
    inputs: Digest
    args: tuple[str, ...]
    output_path: OutputPathField
    field_alias: str
    address: Address
    what: str


@rule(desc="Build OCI helper layer", level=LogLevel.DEBUG)
async def build_helper_layer(
    request: HelperLayerRequest, python: PythonBuildStandaloneBinary
) -> BuiltPackage:
    builders = await create_digest(sandboxed_modules(*_BUILDER_MODULES))
    input_digest = await merge_digests(MergeDigests([builders, request.inputs]))

    relpath = request.output_path.value_or_default(file_ending="tar")
    result = await execute_process(
        Process(
            argv=(python.path, "-c", _BUILDER_MAIN, request.layer, "--output", relpath, *request.args),
            input_digest=input_digest,
            output_files=(relpath,),
            description=f"Build OCI layer with {request.what}: {request.address}",
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        error = result.stderr.decode(errors="replace").strip().removeprefix("error: ")
        raise InvalidFieldException(
            f"Could not build {request.what} from the `{request.field_alias}` field of "
            f"{request.address}: {error}"
        )

    return BuiltPackage(result.output_digest, (BuiltLayerArtifact(relpath=relpath),))


def _entries_spec(entries: tuple[PasswdEntry, ...] | tuple[GroupEntry, ...]) -> CreateDigest:
    # The field has already validated the entries; they're written back in the form it accepts.
    spec = json.dumps([dataclasses.asdict(e) for e in entries], sort_keys=True, separators=(",", ":"))
    return CreateDigest([FileContent(_SPEC, spec.encode())])


@rule(desc="Package OCI layer with /etc/passwd", level=LogLevel.DEBUG)
async def package_passwd_layer(field_set: PasswdLayerFieldSet) -> BuiltPackage:
    spec = await create_digest(_entries_spec(field_set.users.value))
    return await build_helper_layer(
        HelperLayerRequest(
            layer="passwd",
            inputs=spec,
            args=(_SPEC,),
            output_path=field_set.output_path,
            field_alias=PasswdUsersField.alias,
            address=field_set.address,
            what="/etc/passwd",
        ),
        **implicitly(),
    )


@rule(desc="Package OCI layer with /etc/group", level=LogLevel.DEBUG)
async def package_group_layer(field_set: GroupLayerFieldSet) -> BuiltPackage:
    spec = await create_digest(_entries_spec(field_set.groups.value))
    return await build_helper_layer(
        HelperLayerRequest(
            layer="group",
            inputs=spec,
            args=(_SPEC,),
            output_path=field_set.output_path,
            field_alias=GroupEntriesField.alias,
            address=field_set.address,
            what="/etc/group",
        ),
        **implicitly(),
    )


@dataclass(frozen=True)
class DebHelperLayerRequest:
    """Build a helper layer from debs with `helper_layer_builder.main <layer>`."""

    layer: str
    debs: Digest
    deb_files: tuple[str, ...]
    args: tuple[str, ...]
    output_path: OutputPathField
    field_alias: str
    address: Address
    what: str


@rule(desc="Build OCI helper layer from debs", level=LogLevel.DEBUG)
async def build_deb_helper_layer(
    request: DebHelperLayerRequest, zstd_tool: ZstdTool, platform: Platform
) -> BuiltPackage:
    zstd = await download_external_tool(zstd_tool.get_request(platform))
    inputs = await merge_digests(MergeDigests([request.debs, zstd.digest]))
    return await build_helper_layer(
        HelperLayerRequest(
            layer=request.layer,
            inputs=inputs,
            args=("--zstd", zstd.exe, *request.args, *request.deb_files),
            output_path=request.output_path,
            field_alias=request.field_alias,
            address=request.address,
            what=request.what,
        ),
        **implicitly(),
    )


@rule(desc="Package OCI layer with a CA bundle", level=LogLevel.DEBUG)
async def package_cacerts_layer(field_set: CaCertsLayerFieldSet) -> BuiltPackage:
    debs = await resolve_deb_files(DebFilesRequest(field_set.debs, field_set.address))

    # Debs are named `<package>_<version>_<arch>.deb`, so only read the likely candidates, falling
    # back to every deb for other names. The package name is still checked from the control file.
    candidates = [f for f in debs.snapshot.files if os.path.basename(f).startswith(f"{CA_CERTIFICATES}_")]
    debs_digest = debs.snapshot.digest
    if candidates:
        debs_digest = await digest_subset_to_digest(DigestSubset(debs_digest, PathGlobs(candidates)))

    return await build_deb_helper_layer(
        DebHelperLayerRequest(
            layer="cacerts",
            debs=debs_digest,
            deb_files=tuple(candidates or debs.snapshot.files),
            args=(),
            output_path=field_set.output_path,
            field_alias=CaCertsPackagesField.alias,
            address=field_set.address,
            what="the CA bundle",
        ),
        **implicitly(),
    )


@rule(desc="Package OCI layer with locales", level=LogLevel.DEBUG)
async def package_locale_layer(field_set: LocaleLayerFieldSet) -> BuiltPackage:
    debs = await resolve_deb_files(DebFilesRequest(field_set.debs, field_set.address))
    return await build_deb_helper_layer(
        DebHelperLayerRequest(
            layer="locales",
            debs=debs.snapshot.digest,
            deb_files=debs.snapshot.files,
            args=tuple(f"--locale={locale}" for locale in field_set.locales.value),
            output_path=field_set.output_path,
            field_alias=LocalePackagesField.alias,
            address=field_set.address,
            what="the locales",
        ),
        **implicitly(),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, PasswdLayerFieldSet),
        UnionRule(PackageFieldSet, GroupLayerFieldSet),
        UnionRule(PackageFieldSet, CaCertsLayerFieldSet),
        UnionRule(PackageFieldSet, LocaleLayerFieldSet),
    ]
