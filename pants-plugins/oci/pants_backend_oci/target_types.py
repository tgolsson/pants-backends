from __future__ import annotations

from pants.engine.target import BoolField, Dependencies, SpecialCasedDependencies, StringField
from pants.util.strutil import softwrap


class ImageBundle(StringField):
    alias = ""
    help = softwrap(
        """
        The tag to use.
        """
    )


class ImageRepositoryAnonymous(BoolField):
    alias = "anonymous"
    default = False
    help = softwrap(
        """
        Whether the repository access should be anonymous.
        """
    )


class ImageRepository(StringField):
    alias = "repository"

    help = softwrap(
        """
        The repository to import the image from.
        """
    )


class ImageDigest(StringField):
    alias = "digest"
    help = softwrap(
        """
        The tag to use.
        """
    )


class ImageTag(StringField):
    alias = "tag"

    help = softwrap(
        """
        The tag to use for the image.
        """
    )


class ImageDependencies(Dependencies):
    alias = "packages"

    help = softwrap(
        """
        The content to package.
        """
    )


class ImageBase(SpecialCasedDependencies):
    alias = "base"

    help = softwrap(
        """
        The base image to use.
        """
    )


class ImageRunTty(BoolField):
    alias = "terminal"
    default = False

    help = softwrap(
        """Whether the image requires an interactive tty to execute.

        This prevents the image from running in many situations and isn't recommended."""
    )
