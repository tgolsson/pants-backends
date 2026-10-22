# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Rules for the declarative helper layers: `oci_passwd_layer`, `oci_group_layer` and
`oci_cacerts_layer`. The layers are small, so they are built in the rule rather than a process."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import BinaryIO, Callable

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.engine.fs import CreateDigest, DigestSubset, FileContent, PathGlobs
from pants.engine.intrinsics import create_digest, digest_subset_to_digest, get_digest_contents
from pants.engine.rules import collect_rules, rule
from pants.engine.target import InvalidFieldException
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.target_types import CaCertsPackagesField, GroupEntriesField, PasswdUsersField
from pants_backend_oci.util_rules.deb_layer import DebFilesRequest, resolve_deb_files
from pants_backend_oci.util_rules.deb_layer_builder import DebError, load_deb
from pants_backend_oci.util_rules.helper_layer_builder import (
    CA_CERTIFICATES,
    build_cacerts_layer,
    build_group_layer,
    build_passwd_layer,
)
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact


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
async def package_cacerts_layer(field_set: CaCertsLayerFieldSet) -> BuiltPackage:
    debs = await resolve_deb_files(DebFilesRequest(field_set.debs, field_set.address))

    # Debs are named `<package>_<version>_<arch>.deb`, so only read the likely candidates, falling
    # back to every deb for other names. The package name is still checked from the control file.
    candidates = [f for f in debs.snapshot.files if os.path.basename(f).startswith(f"{CA_CERTIFICATES}_")]
    digest = debs.snapshot.digest
    if candidates:
        digest = await digest_subset_to_digest(DigestSubset(digest, PathGlobs(candidates)))
    contents = await get_digest_contents(digest)

    try:
        packages = [load_deb(io.BytesIO(deb.content), source=deb.path) for deb in contents]
    except DebError as e:
        raise InvalidFieldException(f"Could not read the debs of {field_set.address}: {e}") from e

    matching = [package for package in packages if package.name == CA_CERTIFICATES]
    if len(matching) != 1:
        found = ", ".join(p.source for p in matching) or "none"
        raise InvalidFieldException(
            f"The `{CaCertsPackagesField.alias}` field of {field_set.address} must bring exactly one "
            f"`{CA_CERTIFICATES}` package, found: {found}"
        )

    try:
        relpath, layer = _layer(field_set.output_path, lambda out: build_cacerts_layer(matching[0], out))
    except DebError as e:
        raise InvalidFieldException(f"Could not build the CA bundle of {field_set.address}: {e}") from e
    digest = await create_digest(layer)
    return BuiltPackage(digest, (BuiltLayerArtifact(relpath=relpath),))


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, PasswdLayerFieldSet),
        UnionRule(PackageFieldSet, GroupLayerFieldSet),
        UnionRule(PackageFieldSet, CaCertsLayerFieldSet),
    ]
