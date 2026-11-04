from __future__ import annotations

from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    AsyncFieldMixin,
    StringField,
    StringSequenceField,
    Target,
)
from pants.util.strutil import help_text


class PlatformOsField(StringField):
    alias = "os"
    required = True
    valid_choices = ("linux", "windows", "macos")
    help = "The operating system."


class PlatformArchField(StringField):
    alias = "arch"
    required = True
    valid_choices = ("x86_64", "aarch64")
    help = "The CPU architecture."


class PlatformTarget(Target):
    alias = "platform"
    core_fields = (*COMMON_TARGET_FIELDS, PlatformOsField, PlatformArchField)
    help = "A platform to build for, referred to by its target name, which is unique."


class PlatformsField(StringSequenceField, AsyncFieldMixin):
    alias = "platforms"
    default = None
    help = help_text(
        """
        The platforms this target can be built for, by name; unset, the host only. `pants
        package` builds those that are the host or in `[platforms].enabled`.
        """
    )
