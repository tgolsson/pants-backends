from dataclasses import dataclass
from typing import Any

from pants.core.goals.test import (
    TestDebugAdapterRequest,
    TestDebugRequest,
    TestFieldSet,
    TestRequest,
    TestResult,
    TestSubsystem,
)
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import EMPTY_FILE_DIGEST
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants.version import PANTS_SEMVER, Version

from pants_backend_oci.subsystems.test import OciTestSubsystem
from pants_backend_oci.targets import DummySource, ExpectedImageDigest, ImageBase
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundleRequest,
)


@dataclass(frozen=True)
class StructureTestFieldSet(TestFieldSet):
    if PANTS_SEMVER >= Version("2.16.0dev0"):
        required_fields = (
            ExpectedImageDigest,
            ImageBase,
        )
    else:
        required_fields = (
            ExpectedImageDigest,
            ImageBase,
            DummySource,
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

    base = await Get(
        Addresses,
        UnparsedAddressInputs,
        field_set.base.to_unparsed_address_inputs(),
    )

    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(base[0], description_of_origin="<OCI Structure Test>"),
    )

    target = wrapped_target.target
    bundle_request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(target))
    image = await Get(FallibleImageBundle, FallibleImageBundleRequest, bundle_request.request)

    if image.exit_code != 0:
        return TestResult(
            exit_code=image.exit_code,
            stdout=image.stdout,
            stderr=image.stderr,
            stdout_digest=EMPTY_FILE_DIGEST,
            stderr_digest=EMPTY_FILE_DIGEST,
            addresses=(field_set.address,),
            output_setting=test_subsystem.output,
            result_metadata=None,
        )

    output = image.output
    digest = output.image_sha.lstrip("sha256:")
    if digest != field_set.digest.value:
        return TestResult(
            exit_code=1,
            stdout=f"Expected digest {field_set.digest.value} but got {digest}",
            stderr="",
            stdout_digest=EMPTY_FILE_DIGEST,
            stderr_digest=EMPTY_FILE_DIGEST,
            addresses=(field_set.address,),
            output_setting=test_subsystem.output,
            result_metadata=None,
        )

    return TestResult(
        exit_code=0,
        stdout="",
        stderr="",
        stdout_digest=EMPTY_FILE_DIGEST,
        stderr_digest=EMPTY_FILE_DIGEST,
        addresses=(field_set.address,),
        output_setting=test_subsystem.output,
        result_metadata=None,
    )


if not PANTS_SEMVER >= Version("2.16.0dev0"):

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
        UnionRule(TestFieldSet, StructureTestFieldSet),
        *StructureTestRequest.rules(),
        *collect_rules(),
    ]
