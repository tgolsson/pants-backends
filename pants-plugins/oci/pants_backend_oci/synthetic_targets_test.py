from pants.build_graph.address import ResolveError
from pants.core.util_rules import external_tool
from pants.engine.addresses import Address
from pants.testutil.rule_runner import RuleRunner, engine_error

from pants_backend_oci import synthetic_targets
from pants_backend_oci.targets import ImageEmpty


def test_synthetic_target_exists() -> None:
    rule_runner = RuleRunner(
        target_types=[ImageEmpty],
        rules=[
            *external_tool.rules(),
            *synthetic_targets.rules(),
        ],
    )

    rule_runner.write_files({})
    target = rule_runner.get_target(Address("", target_name="empty"))

    assert target is not None


def test_synthetic_target_has_custom_name() -> None:
    from pants_backend_oci import synthetic_targets

    rule_runner = RuleRunner(
        target_types=[ImageEmpty],
        rules=[
            *external_tool.rules(),
            *synthetic_targets.rules(),
        ],
    )

    rule_runner.set_options(["--oci-empty-image-target=custom_name"])

    with engine_error(ResolveError):
        _ = rule_runner.get_target(Address("", target_name="empty"))

    _ = rule_runner.get_target(Address("", target_name="custom_name"))
