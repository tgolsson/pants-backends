from __future__ import annotations

from pants.engine.fs import PathGlobs, Paths
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    MultipleSourcesField,
    SingleSourceField,
    Target,
    TargetFilesGenerator,
)
from pants.util.strutil import softwrap


class OdinSourceField(SingleSourceField):
    """A single Odin source file."""

    expected_file_extensions = (".odin",)


class OdinSourcesField(MultipleSourcesField):
    """A set of Odin source files."""

    alias = "sources"
    uses_source_roots = True
    default = ("*.odin",)

    help = softwrap("""
        A group of Odin source files.
        """)


class OdinDependenciesField(Dependencies):
    """Dependencies for Odin targets."""

    pass


class OdinSourceTarget(Target):
    alias = "odin_source"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinDependenciesField,
        OdinSourceField,
    )
    help = softwrap("""
        A single Odin source file target.
        """)


class OdinSourcesGeneratorTarget(TargetFilesGenerator):
    alias = "odin_sources"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinDependenciesField,
        OdinSourcesField,
    )
    generated_target_cls = OdinSourceTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (OdinDependenciesField, OdinSourceField)
    help = softwrap("""
        Generate an `odin_source` target for each file in the `sources` field.

        Example BUILD file:

            odin_sources(
                name="lib",
                sources=["**/*.odin"],
            )
        """)


class OdinPackageSourcesField(MultipleSourcesField):
    """A set of Odin source files that make up a package."""

    alias = "sources"
    uses_source_roots = True
    default = ("*.odin",)

    help = softwrap("""
        All Odin source files in this package directory.
        
        Defaults to all .odin files in the target's directory, which represents
        a complete Odin package.
        """)


class OdinPackageTarget(Target):
    alias = "odin_package"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinDependenciesField,
        OdinPackageSourcesField,
    )
    help = softwrap("""
        An Odin package target that represents all .odin files in a directory.
        
        This target automatically discovers and includes all .odin files in its directory,
        making it suitable for representing Odin packages which are typically organized
        as all .odin files in a directory sharing the same package declaration.

        Example BUILD file:

            odin_package(
                name="mypackage",
            )
        """)


def targets():
    return [
        OdinSourceTarget,
        OdinSourcesGeneratorTarget,
        OdinPackageTarget,
    ]


def rules():
    return [
        *collect_rules(),
    ]
