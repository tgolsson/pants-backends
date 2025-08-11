from __future__ import annotations

from dataclasses import dataclass

from pants.core.target_types import ResourceSourceField
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, rule
from pants.engine.target import Address, TransitiveTargets, TransitiveTargetsRequest
from pants.util.logging import LogLevel
from pants_backend_odin.target_types import OdinSourceField


@dataclass(frozen=True)
class PrepareOdinSandboxRequest:
    """Request to prepare an Odin sandbox with sources and resources."""

    address: Address


@dataclass(frozen=True)
class PrepareOdinSandboxResult:
    """Result of preparing an Odin sandbox."""

    digest: Digest
    directory: str
    source_files: tuple[str, ...]
    resource_files: tuple[str, ...]


@rule(level=LogLevel.DEBUG, desc="Prepare Odin sandbox with sources and resources")
async def prepare_odin_sandbox(
    request: PrepareOdinSandboxRequest,
) -> PrepareOdinSandboxResult:
    """Prepare an Odin sandbox by collecting sources and resources from dependencies."""

    # Get the dependencies of the target to find the source and resource files
    dependencies = await Get(TransitiveTargets, TransitiveTargetsRequest([request.address]))

    # Collect all source files and resource files from the dependencies
    source_field_sets = []
    resource_field_sets = []
    
    for target in dependencies.closure:
        if target.has_field(OdinSourceField):
            source_field_sets.append(target[OdinSourceField])
        if target.has_field(ResourceSourceField):
            resource_field_sets.append(target[ResourceSourceField])

    # Extract directory from the address
    directory = request.address.spec_path or "."

    # Validate directory path for security
    if ".." in directory or directory.startswith("/"):
        raise Exception(f"Invalid directory path: {directory}")

    # Get the source and resource files
    digests_to_merge = []
    source_files = ()
    resource_files = ()

    if source_field_sets:
        sources_digest = await Get(SourceFiles, SourceFilesRequest(source_field_sets))
        digests_to_merge.append(sources_digest.snapshot.digest)
        source_files = tuple(sources_digest.snapshot.files)

    if resource_field_sets:
        resources_digest = await Get(SourceFiles, SourceFilesRequest(resource_field_sets))
        digests_to_merge.append(resources_digest.snapshot.digest)
        resource_files = tuple(resources_digest.snapshot.files)

    # Merge all digests into a single sandbox digest
    if digests_to_merge:
        merged_digest = await Get(Digest, MergeDigests(digests_to_merge))
    else:
        # Create an empty digest if no files found
        merged_digest = await Get(Digest, MergeDigests([]))

    return PrepareOdinSandboxResult(
        digest=merged_digest,
        directory=directory,
        source_files=source_files,
        resource_files=resource_files,
    )


def rules():
    return [
        *collect_rules(),
    ]