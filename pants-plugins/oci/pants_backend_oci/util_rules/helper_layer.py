# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Rules for the declarative helper layers: `oci_passwd_layer`, `oci_group_layer` and
`oci_cacerts_layer`. The passwd and group layers are small, so they are built in the rule rather
than a process. The CA bundle is built in a process, since reading zstd-compressed debs needs a
`zstd` binary."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import BinaryIO, Callable

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.fs import CreateDigest, DigestSubset, FileContent, MergeDigests, PathGlobs
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
from pants_backend_oci.target_types import CaCertsPackagesField, GroupEntriesField, PasswdUsersField
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
_CACERTS_MAIN = module_main(helper_layer_builder)


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


@rule(desc="Package OCI layer with a CA bundle", level=LogLevel.DEBUG)
async def package_cacerts_layer(
    field_set: CaCertsLayerFieldSet,
    python: PythonBuildStandaloneBinary,
    zstd_tool: ZstdTool,
    platform: Platform,
) -> BuiltPackage:
    debs = await resolve_deb_files(DebFilesRequest(field_set.debs, field_set.address))

    # Debs are named `<package>_<version>_<arch>.deb`, so only read the likely candidates, falling
    # back to every deb for other names. The package name is still checked from the control file.
    candidates = [f for f in debs.snapshot.files if os.path.basename(f).startswith(f"{CA_CERTIFICATES}_")]
    debs_digest = debs.snapshot.digest
    if candidates:
        debs_digest = await digest_subset_to_digest(DigestSubset(debs_digest, PathGlobs(candidates)))

    zstd = await download_external_tool(zstd_tool.get_request(platform))
    builders = await create_digest(sandboxed_modules(*_BUILDER_MODULES))
    input_digest = await merge_digests(MergeDigests([builders, debs_digest, zstd.digest]))

    relpath = field_set.output_path.value_or_default(file_ending="tar")
    result = await execute_process(
        Process(
            argv=(
                python.path,
                "-c",
                _CACERTS_MAIN,
                "--output",
                relpath,
                "--zstd",
                zstd.exe,
                *(candidates or debs.snapshot.files),
            ),
            input_digest=input_digest,
            output_files=(relpath,),
            description=f"Build OCI layer with a CA bundle: {field_set.address}",
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        error = result.stderr.decode(errors="replace").strip().removeprefix("error: ")
        raise InvalidFieldException(
            f"Could not build the CA bundle from the `{CaCertsPackagesField.alias}` field of "
            f"{field_set.address}: {error}"
        )

    return BuiltPackage(result.output_digest, (BuiltLayerArtifact(relpath=relpath),))


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, PasswdLayerFieldSet),
        UnionRule(PackageFieldSet, GroupLayerFieldSet),
        UnionRule(PackageFieldSet, CaCertsLayerFieldSet),
    ]
