"""`pants test` for `cmake_test` targets: the executable Pants compiled and linked (see
`util_rules/executable.py`), run from the libraries' build directory with the target's `args`.
Staged with it: the libraries it links against, the packages in `runtime_package_dependencies`,
and the `files`/`resources` in `dependencies`. A test that fails to build, or whose wrapper
cannot be set up, is a failed test, not a crashed goal. Another backend can run the executable
under a wrapper (`wrapper.py`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pants.core.goals.test import (
    BuildPackageDependenciesRequest,
    TestDebugRequest,
    TestExtraEnv,
    TestFieldSet,
    TestRequest,
    TestResult,
    TestSubsystem,
    build_runtime_package_dependencies,
)
from pants.core.util_rules.env_vars import environment_vars_subset
from pants.engine.env_vars import EnvironmentVarsRequest
from pants.engine.fs import EMPTY_FILE_DIGEST, MergeDigests
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import execute_process, merge_digests
from pants.engine.process import (
    InteractiveProcess,
    Process,
    ProcessCacheScope,
    ProcessExecutionFailure,
)
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import WrappedTargetRequest
from pants.engine.unions import UnionMembership
from pants.option.option_types import SkipOption
from pants.option.subsystem import Subsystem
from pants.util.logging import LogLevel

from pants_backend_cmake.goals.wrapper import CMakeTestWrapperError, cmake_test_wrapper
from pants_backend_cmake.target_types import (
    CMakeTestArgsField,
    CMakeTestExtraEnvVarsField,
    CMakeTestRuntimePackageDependenciesField,
    CMakeTestSourcesField,
    CMakeTestTimeoutField,
)
from pants_backend_cmake.util_rules.executable import TestBuildError, TestBuildRequest, build_test
from pants_backend_cmake.util_rules.scan import ScanError


class CMakeTestSubsystem(Subsystem):
    options_scope = "cmake-test-runner"
    name = "CMake tests"
    help = "Builds and runs `cmake_test` executables."

    skip = SkipOption("test")


@dataclass(frozen=True)
class CMakeTestFieldSet(TestFieldSet):
    required_fields = (CMakeTestSourcesField, CMakeTestArgsField)

    sources: CMakeTestSourcesField
    args: CMakeTestArgsField
    timeout: CMakeTestTimeoutField
    extra_env_vars: CMakeTestExtraEnvVarsField
    runtime_package_dependencies: CMakeTestRuntimePackageDependenciesField


class CMakeTestRequest(TestRequest):
    tool_subsystem = CMakeTestSubsystem
    field_set_type = CMakeTestFieldSet
    supports_debug = True


async def _test_process(
    field_set: CMakeTestFieldSet,
    test_subsystem: TestSubsystem,
    test_extra_env: TestExtraEnv,
    union_membership: UnionMembership,
) -> Process:
    built, packages, field_env = await concurrently(
        build_test(TestBuildRequest(field_set.address), **implicitly()),
        build_runtime_package_dependencies(
            BuildPackageDependenciesRequest(field_set.runtime_package_dependencies)
        ),
        environment_vars_subset(EnvironmentVarsRequest(field_set.extra_env_vars.value or ()), **implicitly()),
    )
    wrapped = await resolve_target(
        WrappedTargetRequest(field_set.address, description_of_origin="<infallible>"), **implicitly()
    )
    wrapper = await cmake_test_wrapper(wrapped.target, union_membership)
    project = built.project
    input_digest = await merge_digests(
        MergeDigests([project.build_dir_digest, built.runtime, built.data, *(p.digest for p in packages)])
    )
    executable = built.executable[len(project.build_dir) + 1 :]
    return Process(
        argv=(*wrapper.argv_prefix, f"./{executable}", *(field_set.args.value or ())),
        description=f"Run {field_set.address}",
        input_digest=input_digest,
        immutable_input_digests=wrapper.immutable_input_digests,
        working_directory=project.build_dir,
        env={**test_extra_env.env, **field_env, **wrapper.env},
        timeout_seconds=field_set.timeout.calculate_from_global_options(test_subsystem),
        cache_scope=ProcessCacheScope.PER_SESSION if test_subsystem.force else ProcessCacheScope.SUCCESSFUL,
        level=LogLevel.DEBUG,
    )


def _build_failure(
    field_set: CMakeTestFieldSet, error: Exception, test_subsystem: TestSubsystem
) -> TestResult:
    return TestResult(
        exit_code=1,
        stdout_bytes=b"",
        stdout_digest=EMPTY_FILE_DIGEST,
        stderr_bytes=str(error).encode(),
        stderr_digest=EMPTY_FILE_DIGEST,
        addresses=(field_set.address,),
        output_setting=test_subsystem.output,
        result_metadata=None,
    )


@rule(desc="Run CMake test", level=LogLevel.DEBUG)
async def run_cmake_test(
    batch: CMakeTestRequest.Batch[CMakeTestFieldSet, Any],
    test_subsystem: TestSubsystem,
    test_extra_env: TestExtraEnv,
    union_membership: UnionMembership,
) -> TestResult:
    field_set = batch.single_element
    try:
        process = await _test_process(field_set, test_subsystem, test_extra_env, union_membership)
    except (ProcessExecutionFailure, ScanError, TestBuildError, CMakeTestWrapperError) as error:
        return _build_failure(field_set, error, test_subsystem)
    results = []
    for _ in range(test_subsystem.attempts_default):
        result = await execute_process(process, **implicitly())
        results.append(result)
        if result.exit_code == 0:
            break
    return TestResult.from_fallible_process_result(
        tuple(results), address=field_set.address, output_setting=test_subsystem.output
    )


@rule(desc="Set up CMake test for debugging", level=LogLevel.DEBUG)
async def debug_cmake_test(
    batch: CMakeTestRequest.Batch[CMakeTestFieldSet, Any],
    test_subsystem: TestSubsystem,
    test_extra_env: TestExtraEnv,
    union_membership: UnionMembership,
) -> TestDebugRequest:
    process = await _test_process(batch.single_element, test_subsystem, test_extra_env, union_membership)
    return TestDebugRequest(InteractiveProcess.from_process(process, forward_signals_to_process=False))


def rules():
    return [*collect_rules(), *CMakeTestRequest.rules()]
