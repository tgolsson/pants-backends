from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import DependenciesRequest, Targets
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinDependenciesField, OdinDefinesField, OdinSourceField


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
) -> OdinBuildResult:
    """Internal rule to build an Odin package with the Odin compiler."""
    
    downloaded_odin = await Get(DownloadedExternalTool, ExternalToolRequest, odin.get_request(platform))
    
    input_digest = await Get(
        Digest,
        MergeDigests([
            downloaded_odin.digest,
            request.sources_digest,
        ])
    )
    
    # Build the odin build command
    argv = [downloaded_odin.exe, "build", request.directory]
    
    # Add defines to the command line
    for define in request.defines:
        argv.extend(["-define", define])
    
    # Add output flag to build binary in current directory
    argv.extend(["-out", f"{request.directory}.bin"])
    
    process_result = await Get(
        ProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            description=f"Build Odin package {request.address}",
            output_files=(f"{request.directory}.bin",),
        ),
    )
    
    return OdinBuildResult(
        digest=process_result.output_digest,
        success=process_result.exit_code == 0,
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
        # No source files found, return empty package
        return BuiltPackage(Digest(), tuple())
    
    # Get the source files
    sources_digest = await Get(SourceFiles, SourceFilesRequest(source_field_sets))
    
    # Extract directory from the field_set address
    directory = field_set.address.spec_path or "."
    
    # Create build request
    build_request = OdinBuildRequest(
        address=field_set.address.spec,
        sources_digest=sources_digest.snapshot.digest,
        defines=tuple(field_set.defines.value or ()),
        directory=directory,
    )
    
    # Build the package
    build_result = await Get(OdinBuildResult, OdinBuildRequest, build_request)
    
    if not build_result.success:
        raise Exception(f"Failed to build Odin package {field_set.address}")
    
    return BuiltPackage(build_result.digest, tuple())


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, OdinPackageFieldSet),
    ]