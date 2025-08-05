from __future__ import annotations

from pants.engine.fs import PathGlobs, Paths
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    InferDependenciesRequest,
    InferredDependencies,
    MultipleSourcesField,
    SingleSourceField,
    SpecialCasedDependencies,
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


class OdinPackageSourcesField(SpecialCasedDependencies):
    """Odin sources that are part of this package."""

    alias = "sources"
    help = softwrap("""
        All Odin source files in this package directory.
        
        This field is automatically populated with all .odin files found in the target's directory.
        You typically don't need to set this field manually.
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


class InferOdinPackageSourcesRequest(InferDependenciesRequest):
    infer_from = OdinPackageSourcesField


@rule
async def infer_odin_package_sources(request: InferOdinPackageSourcesRequest) -> InferredDependencies:
    """Automatically discover all .odin files in the package directory."""
    # Get all .odin files in the target's directory
    paths = await Get(
        Paths,
        PathGlobs,
        PathGlobs([f"{request.sources_field.address.spec_path}/*.odin"]),
    )
    
    # Create addresses for each .odin file to depend on
    addresses = []
    for path in paths.files:
        # Convert file path to address (without the .odin extension)
        file_name = path.split("/")[-1]
        if file_name.endswith(".odin"):
            addresses.append(request.sources_field.address.create_generated(file_name[:-5]))
    
    return InferredDependencies(addresses)


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
