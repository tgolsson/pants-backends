from dataclasses import dataclass
from typing import Any

from pants.core.goals.test import (
    TestDebugAdapterRequest,
    TestDebugRequest,
    TestExtraEnv,
    TestFieldSet,
    TestRequest,
    TestResult,
    TestSubsystem,
)
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import Target, WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystems.test import OciTestSubsystem
from pants_backend_oci.targets import ExpectedImageDigest, ImageBase
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundleRequest,
)


@dataclass(frozen=True)
class StructureTestFieldSet(TestFieldSet):
    required_fields = (
        ExpectedImageDigest,
        ImageBase,
    )
    digest: ExpectedImageDigest
    base: ImageBase


@dataclass(frozen=True)
class StructureTestRequest(TestRequest):
    tool_subsystem = OciTestSubsystem
    field_set_type = StructureTestFieldSet


@rule(desc="Executing structure test", level=LogLevel.DEBUG)
async def run_oci_structure_test(
    batch: StructureTestRequest.Batch[StructureTestFieldSet, Any],
    test_subsystem: TestSubsystem,
    oci_test_subsystem: OciTestSubsystem,
) -> TestResult:
    field_set = batch.single_element
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(field_set.base, description_of_origin="package_oci_image"),
    )
    target = wrapped_target.target
    bundle_request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(target))
    image = Get(FallibleImageBundle, FallibleImageBundleRequest, bundle_request.request)

    if image.exit_code != 0:
        return TestResult(
            exit_code=image.exit_code,
            stdout=image.stdout,
            stderr=image.stderr,
            addresses=(field_set.address,),
        )

    output = image.output
    digest = output.digest
    if digest != field_set.digest:
        return TestResult(
            exit_code=1,
            stdout=f"Expected digest {field_set.digest} but got {digest}",
            stderr="",
            addresses=(field_set.address,),
        )

    return TestResult(
        exit_code=0,
        stdout="",
        stderr="",
        addresses=(field_set.address,),
        output_setting=test_subsystem.output,
    )


@rule
async def setup_example_debug_test(
    batch: StructureTestRequest.Batch[StructureTestFieldSet, Any],
) -> TestDebugRequest:
    raise NotImplementedError()


@rule
async def setup_example_debug_adapter_test(
    batch: StructureTestRequest.Batch[StructureTestFieldSet, Any],
) -> TestDebugAdapterRequest:
    raise NotImplementedError()


def rules():
    return [
        # Add to any other existing rules here:
        UnionRule(TestFieldSet, StructureTestFieldSet),
        *StructureTestRequest.rules(),
        *collect_rules(),
    ]
