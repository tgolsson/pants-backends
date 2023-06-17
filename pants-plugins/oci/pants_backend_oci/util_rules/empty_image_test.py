from pants.core.util_rules import external_tool
from pants.engine import process
from pants.engine.addresses import Address
from pants.testutil.rule_runner import QueryRule, RuleRunner
from pants.version import PANTS_SEMVER, Version

from pants_backend_oci import synthetic_targets
from pants_backend_oci.targets import ImageEmpty
from pants_backend_oci.tools import process as fprocess
from pants_backend_oci.util_rules import empty_image_bundle, image_bundle, oci_sha


def test_empty_image_has_fixed_sha() -> None:
    GOLDEN = "sha256:e0039d9a394788147eb854e5efe5429723352c288faa47de3bb1abbd53a7f7bb"
    rule_runner = RuleRunner(
        target_types=[ImageEmpty],
        rules=[
            *empty_image_bundle.rules(),
            *external_tool.rules(),
            *oci_sha.rules(),
            *process.rules(),
            *fprocess.rules(),
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
    assert result.output.image_sha == GOLDEN
