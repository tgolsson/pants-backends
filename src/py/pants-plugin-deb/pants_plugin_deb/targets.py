from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Address,
    Dependencies,
    InvalidFieldException,
    StringField,
    StringSequenceField,
    Target,
)
from pants.util.strutil import pluralize, softwrap


class DebianPackagesField(StringSequenceField):
    alias = "packages"
    help = softwrap(
        """
        The list of Debian packages to lock.
        """
    )


class DebianPackageField(StringField):
    alias = "package"
    help = softwrap(
        """
        The package name.
        """
    )


class DebianResolveField(StringField):
    alias = "resolve"
    required = True
    help = softwrap(
        """
        The resolve name used by the lockfile.
        """
    )


class DebianArchitecturesField(StringSequenceField):
    alias = "architectures"
    valid_choices = ("amd64", "arm64")
    help = softwrap(
        """
        The architectures of the Debian package. This is a required field.
        """
    )


class DebianSnapshotField(StringField):
    alias = "snapshot"
    help = softwrap(
        """
        The snapshot timestamp of the Debian package index. This is a required field.
        """
    )


class DebianReleaseField(StringField):
    alias = "release"
    help = softwrap(
        """
        The release of Debian to fetch packages for, e.g. bullseye, bookworm, etc.
        """
    )


class DebianPoolsField(StringSequenceField):
    alias = "pools"
    help = softwrap(
        """
        The pools of the Debian package.
        """
    )

    valid_choices = (
        "main",
        "contrib",
        "non-free",
        "non-free-firmware",
    )
    default = ["main", "contrib"]


class DebPackage(Target):
    alias = "deb_package"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        DebianArchitecturesField,
        DebianPoolsField,
        DebianReleaseField,
        DebianResolveField,
        DebianSnapshotField,
        DebianPackageField,
    )
    help = softwrap(
        """
        A Debian package that can be installed in containers.
        """
    )


def targets():
    return [
        DebPackage,
    ]
