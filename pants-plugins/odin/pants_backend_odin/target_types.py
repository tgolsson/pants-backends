from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import OutputPathField
from pants.engine.rules import collect_rules
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    FieldSet,
    InferDependenciesRequest,
    MultipleSourcesField,
    SingleSourceField,
    StringSequenceField,
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


class OdinDefinesField(StringSequenceField):
    """Build-time defines for Odin compilation."""

    alias = "defines"
    help = softwrap("""
        A list of build-time defines to pass to the Odin compiler.
        Each define should be in the format 'KEY=VALUE'.

        Example:
            defines=["DEBUG=true", "VERSION=1.0.0"]
        """)


class OdinSourceTarget(Target):
    alias = "odin_source"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinSourceField,
    )
    help = softwrap("""
        A single Odin source file target.
        """)


class OdinSourcesGeneratorTarget(TargetFilesGenerator):
    alias = "odin_sources"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinSourcesField,
    )
    generated_target_cls = OdinSourceTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (OdinSourceField,)
    help = softwrap("""
        Generate an `odin_source` target for each file in the `sources` field.

        Example BUILD file:

            odin_sources(
                name="lib",
                sources=["**/*.odin"],
            )
        """)


class OdinPackageTarget(Target):
    alias = "odin_package"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinDependenciesField,
        OdinDefinesField,
        OutputPathField,
    )
    help = softwrap("""
        An Odin package target that represents all .odin files in a directory.

        This target automatically depends on all odin_source targets in its directory,
        making it suitable for representing Odin packages which are typically organized
        as all .odin files in a directory sharing the same package declaration.

        Example BUILD file:

            odin_package(
                name="mypackage",
                defines=["DEBUG=true"],
            )
        """)


@dataclass(frozen=True)
class OdinPackageDependenciesInferenceFieldSet(FieldSet):
    required_fields = (OdinDependenciesField,)

    dependencies: OdinDependenciesField


class InferOdinPackageDependenciesRequest(InferDependenciesRequest):
    infer_from = OdinPackageDependenciesInferenceFieldSet


class OdinTestTarget(Target):
    alias = "odin_test"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OdinDependenciesField,
        OdinDefinesField,
    )
    help = softwrap("""
        An Odin test target that represents test files in a directory.

        This target automatically depends on all odin_source targets in its directory,
        builds them with test mode, and runs the resulting test executable.

        Example BUILD file:

            odin_test(
                name="mytest",
                defines=["DEBUG=true"],
            )
        """)


def targets():
    return [
        OdinSourceTarget,
        OdinSourcesGeneratorTarget,
        OdinPackageTarget,
        OdinTestTarget,
    ]


def rules():
    return [
        *collect_rules(),
    ]
