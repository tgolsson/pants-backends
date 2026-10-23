# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Rules for the declarative helper layers: `oci_passwd_layer`, `oci_group_layer`,
`oci_cacerts_layer` and `oci_locale_layer`. The passwd and group layers are small, so they are built
in the rule rather than a process. The CA bundle and locales are built in a process, since reading
zstd-compressed debs needs a `zstd` binary."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import BinaryIO, Callable

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
    build_group_layer,
    build_passwd_layer,
)
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules

# The builders are copied into the sandbox under their package path, so their imports still resolve.
_BUILDER_MODULES = (deb_layer_builder, helper_layer_builder)
_BUILDER_MAIN = module_main(helper_layer_builder)


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


def _layer(output_path: OutputPathField, build: Callable[[BinaryIO], None]) -> tuple[str, CreateDigest]:
    out = io.BytesIO()
    build(out)
    relpath = output_path.value_or_default(file_ending="tar")
    return relpath, CreateDigest([FileContent(relpath, out.getvalue())])


@rule(desc="Package OCI layer with /etc/passwd", level=LogLevel.DEBUG)
async def package_passwd_layer(field_set: PasswdLayerFieldSet) -> BuiltPackage:
    relpath, layer = _layer(field_set.output_path, lambda out: build_passwd_layer(field_set.users.value, out))
    digest = await create_digest(layer)
    return BuiltPackage(digest, (BuiltLayerArtifact(relpath=relpath),))


@rule(desc="Package OCI layer with /etc/group", level=LogLevel.DEBUG)
async def package_group_layer(field_set: GroupLayerFieldSet) -> BuiltPackage:
    relpath, layer = _layer(field_set.output_path, lambda out: build_group_layer(field_set.groups.value, out))
    digest = await create_digest(layer)
    return BuiltPackage(digest, (BuiltLayerArtifact(relpath=relpath),))


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
    request: DebHelperLayerRequest,
    python: PythonBuildStandaloneBinary,
    zstd_tool: ZstdTool,
    platform: Platform,
) -> BuiltPackage:
    zstd = await download_external_tool(zstd_tool.get_request(platform))
    builders = await create_digest(sandboxed_modules(*_BUILDER_MODULES))
    input_digest = await merge_digests(MergeDigests([builders, request.debs, zstd.digest]))

    relpath = request.output_path.value_or_default(file_ending="tar")
    result = await execute_process(
        Process(
            argv=(
                python.path,
                "-c",
                _BUILDER_MAIN,
                request.layer,
                "--output",
                relpath,
                "--zstd",
                zstd.exe,
                *request.args,
                *request.deb_files,
            ),
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
