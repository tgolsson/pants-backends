"""`pants test` for `cc_test`: runs what `util_rules/executable.py` builds. A test that fails
to build, or whose wrapper cannot be set up, is a failed test rather than a crashed goal."""

from __future__ import annotations

import os
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

from pants_backend_cc.goals.wrapper import CcTestWrapperError, cc_test_wrapper
from pants_backend_cc.target_types import (
    CcTestArgsField,
    CcTestExtraEnvVarsField,
    CcTestRuntimePackageDependenciesField,
    CcTestSourcesField,
    CcTestTimeoutField,
)
from pants_backend_cc.util_rules.executable import (
    ExecutableBuildError,
    TestBuildRequest,
    build_test,
)
from pants_backend_cc.util_rules.library import CcLibraryError
from pants_backend_cc.util_rules.scan import ScanError


class CcTestSubsystem(Subsystem):
    options_scope = "cc-test-runner"
    name = "C/C++ tests"
    help = "Builds and runs `cc_test` executables."

    skip = SkipOption("test")


@dataclass(frozen=True)
class CcTestFieldSet(TestFieldSet):
    required_fields = (CcTestSourcesField, CcTestArgsField)

    sources: CcTestSourcesField
    args: CcTestArgsField
    timeout: CcTestTimeoutField
    extra_env_vars: CcTestExtraEnvVarsField
    runtime_package_dependencies: CcTestRuntimePackageDependenciesField


class CcTestRequest(TestRequest):
    tool_subsystem = CcTestSubsystem
    field_set_type = CcTestFieldSet
    supports_debug = True


async def _test_process(
    field_set: CcTestFieldSet,
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
    wrapper = await cc_test_wrapper(wrapped.target, union_membership)
    input_digest = await merge_digests(
        MergeDigests([built.runtime, built.data, *(p.digest for p in packages)])
    )
    executable = os.path.relpath(built.executable, built.cwd or ".")
    return Process(
        argv=(*wrapper.argv_prefix, f"./{executable}", *(field_set.args.value or ())),
        description=f"Run {field_set.address}",
        input_digest=input_digest,
        immutable_input_digests=wrapper.immutable_input_digests,
        working_directory=built.cwd or None,
        env={**test_extra_env.env, **field_env, **wrapper.env},
        timeout_seconds=field_set.timeout.calculate_from_global_options(test_subsystem),
        cache_scope=ProcessCacheScope.PER_SESSION if test_subsystem.force else ProcessCacheScope.SUCCESSFUL,
        level=LogLevel.DEBUG,
    )


def _build_failure(field_set: CcTestFieldSet, error: Exception, test_subsystem: TestSubsystem) -> TestResult:
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


@rule(desc="Run C/C++ test", level=LogLevel.DEBUG)
async def run_cc_test(
    batch: CcTestRequest.Batch[CcTestFieldSet, Any],
    test_subsystem: TestSubsystem,
    test_extra_env: TestExtraEnv,
    union_membership: UnionMembership,
) -> TestResult:
    field_set = batch.single_element
    try:
        process = await _test_process(field_set, test_subsystem, test_extra_env, union_membership)
    except (
        ProcessExecutionFailure,
        ScanError,
        ExecutableBuildError,
        CcLibraryError,
        CcTestWrapperError,
    ) as error:
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


@rule(desc="Set up C/C++ test for debugging", level=LogLevel.DEBUG)
async def debug_cc_test(
    batch: CcTestRequest.Batch[CcTestFieldSet, Any],
    test_subsystem: TestSubsystem,
    test_extra_env: TestExtraEnv,
    union_membership: UnionMembership,
) -> TestDebugRequest:
    process = await _test_process(batch.single_element, test_subsystem, test_extra_env, union_membership)
    return TestDebugRequest(InteractiveProcess.from_process(process, forward_signals_to_process=False))


def rules():
    return [*collect_rules(), *CcTestRequest.rules()]
