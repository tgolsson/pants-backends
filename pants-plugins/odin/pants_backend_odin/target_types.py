from __future__ import annotations

from pants.engine.rules import collect_rules
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    MultipleSourcesField,
    SingleSourceField,
    Target,
    TargetGenerator,
)
from pants.util.strutil import softwrap


class OdinSourceField(SingleSourceField):
    """A single Odin source file."""

    expected_file_extensions = (".odin",)


class OdinSourcesField(MultipleSourcesField):
    """A set of Odin source files."""

    alias = "sources"
    uses_source_roots = False
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


class OdinSourcesGeneratorTarget(TargetGenerator):
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


def targets():
    return [
        OdinSourceTarget,
        OdinSourcesGeneratorTarget,
    ]


def rules():
    return [
        *collect_rules(),
    ]