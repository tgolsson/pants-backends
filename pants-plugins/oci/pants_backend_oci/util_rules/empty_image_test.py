from pants.core.util_rules import adhoc_binaries, external_tool
from pants.engine import process
from pants.engine.addresses import Address
from pants.engine.platform import Platform
from pants.testutil.rule_runner import QueryRule, RuleRunner

from pants_backend_oci import synthetic_targets
from pants_backend_oci.targets import ImageEmpty
from pants_backend_oci.util_rules import assemble, empty_image_bundle, image_bundle, sandboxed

# The empty image is for the host's architecture.
GOLDEN = {
    "amd64": "sha256:a74e533f90a88cb622c1ae73cf8caa0e483f5ebef47eb6fa1359d5f4bf1cfa2e",
    "arm64": "sha256:b238674ccd5bb1fabc0f88a8ba7258ca38ccc8c8da9ca2e1b1aca2a53f999632",
}


def test_empty_image_has_fixed_sha() -> None:
    rule_runner = RuleRunner(
        target_types=[ImageEmpty],
        rules=[
            *empty_image_bundle.rules(),
            *external_tool.rules(),
            *assemble.rules(),
            *adhoc_binaries.rules(),
            *sandboxed.rules(),
            *process.rules(),
            *synthetic_targets.rules(),
            QueryRule(
                image_bundle.FallibleImageBundle,
                [
                    empty_image_bundle.ImageBundleEmptyRequest,
                ],
            ),
        ],
    )

    files = {"BUILD": ""}

    rule_runner.write_files(files)
    target = rule_runner.get_target(Address("", target_name="empty"))

    request = empty_image_bundle.ImageBundleEmptyRequest(target)
    result: image_bundle.FallibleImageBundle = rule_runner.request(
        image_bundle.FallibleImageBundle, [request]
    )

    assert result.exit_code == 0
    architecture = "arm64" if Platform.create_for_localhost().value.endswith("arm64") else "amd64"
    assert result.output.image_sha == GOLDEN[architecture]
