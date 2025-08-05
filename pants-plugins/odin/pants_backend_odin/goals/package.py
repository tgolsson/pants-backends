from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.system_binaries import (
    BashBinary,
    BinaryPath,
    BinaryPathRequest,
    BinaryPaths,
    BinaryPathTest,
    BinaryShims,
    BinaryShimsRequest,
    SystemBinariesSubsystem,
)
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import DependenciesRequest, Targets
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinDefinesField, OdinDependenciesField, OdinSourceField


@dataclass(frozen=True)
class OdinBuildRequest:
    """Internal request to build an Odin package."""

    address: str
    sources_digest: Digest
    defines: tuple[str, ...]
    directory: str


@dataclass(frozen=True)
class OdinBuildResult:
    """Result of building an Odin package."""

    digest: Digest
    success: bool


@dataclass(frozen=True)
class OdinPackageFieldSet(PackageFieldSet):
    required_fields = (OdinDependenciesField,)

    dependencies: OdinDependenciesField
    defines: OdinDefinesField


@rule(level=LogLevel.DEBUG, desc="Build Odin package")
async def build_odin_package(
    request: OdinBuildRequest,
    odin: OdinTool,
    platform: Platform,
    system_binaries_environment: SystemBinariesSubsystem.EnvironmentAware,
) -> OdinBuildResult:
    """Internal rule to build an Odin package with the Odin compiler."""

    binary_shims = await Get(
        BinaryShims,
        BinaryShimsRequest.for_binaries(
            "cp",
            "cc",
            "ld",
            "as",
            "ar",
            "realpath",
            "clang",
            rationale="Odin",
            search_path=system_binaries_environment.system_binary_paths,
        ),
    )

    downloaded_odin = await Get(DownloadedExternalTool, ExternalToolRequest, odin.get_request(platform))

    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                downloaded_odin.digest,
                request.sources_digest,
                binary_shims.digest,
            ]
        ),
    )

    # Build the odin build command
    argv = [downloaded_odin.exe, "build", request.directory]

    # Add defines to the command line
    for define in request.defines:
        argv.append(f"-define:{define}")

    # Add output flag to build binary in current directory
    argv.append(f"-out:{request.directory}/{request.address.target_name}")

    process_result = await Get(
        ProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            description=f"Build Odin package {request.address}",
            output_files=(f"{request.directory}/{request.address.target_name}",),
            env={"PATH": f"{binary_shims.path_component}"},
            immutable_input_digests={
                **binary_shims.immutable_input_digests,
            },
        ),
    )

    return OdinBuildResult(
        digest=process_result.output_digest,
        success=True,
    )


@rule(desc="Package Odin application")
async def package_odin_application(field_set: OdinPackageFieldSet) -> BuiltPackage:
    """Package an Odin application by building it with the Odin compiler."""

    # Get the dependencies of the odin_package target to find the source files
    dependencies = await Get(Targets, DependenciesRequest, DependenciesRequest(field_set.dependencies))

    # Collect all source files from the dependencies
    source_field_sets = []
    for target in dependencies:
        if not target.has_field(OdinSourceField):
            continue
        source_field_sets.append(target[OdinSourceField])

    if not source_field_sets:
        # No source files found, this could be a configuration error
        raise Exception(
            f"No Odin source files found for package {field_set.address}. "
            f"Make sure the odin_package target has dependencies on odin_source targets."
        )

    # Get the source files
    sources_digest = await Get(SourceFiles, SourceFilesRequest(source_field_sets))

    # Extract directory from the field_set address
    directory = field_set.address.spec_path or "."

    # Validate directory path for security
    if ".." in directory or directory.startswith("/"):
        raise Exception(f"Invalid directory path: {directory}")

    # Create build request
    build_request = OdinBuildRequest(
        address=field_set.address,
        sources_digest=sources_digest.snapshot.digest,
        defines=tuple(field_set.defines.value or ()),
        directory=directory,
    )

    # Build the package
    build_result = await Get(OdinBuildResult, OdinBuildRequest, build_request)

    if not build_result.success:
        raise Exception(f"Failed to build Odin package {field_set.address}")
    artifact = BuiltPackageArtifact(relpath=str(output_filename))
    return BuiltPackage(build_result.digest, tuple())


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, OdinPackageFieldSet),
    ]
