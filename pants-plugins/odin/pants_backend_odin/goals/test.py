from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.test import ShowOutput, TestFieldSet, TestRequest, TestResult, TestSubsystem
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.system_binaries import (
    BinaryShims,
    BinaryShimsRequest,
    SystemBinariesSubsystem,
)
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.target import DependenciesRequest, FieldSet, Targets
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants_backend_odin.goals.package import OdinBuildRequest, OdinBuildResult
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinDefinesField, OdinDependenciesField, OdinSourceField


@dataclass(frozen=True)
class OdinTestFieldSet(FieldSet):
    required_fields = (OdinDependenciesField,)

    dependencies: OdinDependenciesField
    defines: OdinDefinesField


@dataclass(frozen=True)
class OdinTestRequest(TestRequest):
    field_set_type = OdinTestFieldSet
    tool_subsystem = OdinTool


@rule(level=LogLevel.DEBUG, desc="Run Odin tests")
async def run_odin_tests(
    request: OdinTestRequest.Batch[OdinTestFieldSet, None],
    odin: OdinTool,
    platform: Platform,
    test_subsystem: TestSubsystem,
    system_binaries_environment: SystemBinariesSubsystem.EnvironmentAware,
) -> TestResult:
    """Run Odin tests by building with test mode and executing the test binary."""
    assert len(request.elements) == 1
    field_set = request.elements[0]
    if odin.skip:
        return TestResult.skip(field_set.address)

    # Get the dependencies of the odin_test target to find the source files
    dependencies = await Get(Targets, DependenciesRequest, DependenciesRequest(field_set.dependencies))

    # Collect all source files from the dependencies
    source_field_sets = []
    for target in dependencies:
        if not target.has_field(OdinSourceField):
            continue
        source_field_sets.append(target[OdinSourceField])

    if not source_field_sets:
        # No source files found, this could be a configuration error
        return TestResult.error(
            field_set.address,
            stdout="",
            stderr=f"No Odin source files found for test {field_set.address}. "
            f"Make sure the odin_test target has dependencies on odin_source targets.",
        )

    # Get the source files
    sources_digest = await Get(SourceFiles, SourceFilesRequest(source_field_sets))

    # Extract directory from the field_set address
    directory = field_set.address.spec_path or "."

    # Validate directory path for security
    if ".." in directory or directory.startswith("/"):
        return TestResult.error(
            field_set.address,
            stdout="",
            stderr=f"Invalid directory path: {directory}",
        )

    # Create test executable name
    test_executable = f"{field_set.address.target_name}_test"

    # Create build request for test mode
    build_request = OdinBuildRequest(
        address=str(field_set.address),
        sources_digest=sources_digest.snapshot.digest,
        defines=tuple(field_set.defines.value or ()),
        directory=directory,
        output_path=test_executable,
        mode="test",
    )

    # Build the test
    build_result = await Get(OdinBuildResult, OdinBuildRequest, build_request)

    if not build_result.success:
        return TestResult.error(
            field_set.address,
            stdout="",
            stderr=f"Failed to build Odin test {field_set.address}",
        )

    # Run the test
    test_result = await Get(
        FallibleProcessResult,
        Process(
            argv=[f"./{test_executable}"],
            input_digest=build_result.digest,
            description=f"Run Odin test {field_set.address}",
        ),
    )

    return TestResult.from_fallible_process_result(
        (test_result,),
        address=field_set.address,
        output_setting=ShowOutput.ALL if test_result.exit_code != 0 else ShowOutput.FAILED,
    )


def rules():
    return [
        *collect_rules(),
        *OdinTestRequest.rules(),
    ]
