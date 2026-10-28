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


class CcToolchainHostField(StringField):
    alias = "host"
    required = True
    help = "The platform this toolchain runs on, by name."


class CcToolchainTargetField(StringField):
    alias = "target"
    required = True
    help = "The platform this toolchain builds for, by name."


class CcToolchainCcField(StringField):
    alias = "cc"
    required = True
    help = "The C compiler: a name looked up on the system search path, or an absolute path."


class CcToolchainCxxField(StringField):
    alias = "cxx"
    required = True
    help = "The C++ compiler: a name looked up on the system search path, or an absolute path."


class CcToolchainArField(StringField):
    alias = "ar"
    required = True
    help = "The archiver: a name looked up on the system search path, or an absolute path."


class CcToolchainTarget(Target):
    alias = "cc_toolchain"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CcToolchainHostField,
        CcToolchainTargetField,
        CcToolchainCcField,
        CcToolchainCxxField,
        CcToolchainArField,
    )
    help = "The C and C++ tools that build for `target` on `host`, native pairs included."


class PlatformsField(StringSequenceField, AsyncFieldMixin):
    alias = "platforms"
    default = None
    help = help_text(
        """
        The platforms this target can be built for, by name; unset, the host only. `pants
        package` builds those that are the host or in `[platforms].enabled`.
        """
    )
