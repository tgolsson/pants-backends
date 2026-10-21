from __future__ import annotations

import io
import tarfile

import pytest
from pants.core.goals import package as package_goal
from pants.core.goals.package import BuiltPackage
from pants.core.target_types import FileTarget, ResourceTarget
from pants.core.target_types import rules as core_target_type_rules
from pants.core.util_rules import adhoc_binaries, source_files
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.testutil.rule_runner import QueryRule, RuleRunner, engine_error

from pants_backend_oci.targets import DebLayer
from pants_backend_oci.util_rules import archive, deb_layer, layer
from pants_backend_oci.util_rules.deb_layer_builder_test import BAR, FOO, _dir, make_deb


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        target_types=[DebLayer, FileTarget, ResourceTarget],
        rules=[
            *archive.rules(),
            *deb_layer.rules(),
            *layer.rules(),
            *package_goal.rules(),
            *adhoc_binaries.rules(),
            *source_files.rules(),
            *core_target_type_rules(),
            QueryRule(BuiltPackage, [deb_layer.DebLayerPackageFieldSet]),
            QueryRule(layer.ImageLayer, [layer.ImageLayerRequest]),
        ],
    )


def layer_names(rule_runner: RuleRunner, built: BuiltPackage) -> list[str]:
    (content,) = rule_runner.request(DigestContents, [built.digest])
    with tarfile.open(fileobj=io.BytesIO(content.content)) as tar:
        return tar.getnames()


def package(rule_runner: RuleRunner, name: str) -> BuiltPackage:
    target = rule_runner.get_target(Address("debs", target_name=name))
    field_set = deb_layer.DebLayerPackageFieldSet.create(target)
    return rule_runner.request(BuiltPackage, [field_set])


def test_packages_layer_from_debs(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "debs/foo_1.0-1_amd64.deb": FOO,
            "debs/bar_1.0-1_amd64.deb": BAR,
            "debs/BUILD": """
file(name="foo", source="foo_1.0-1_amd64.deb")
file(name="bar", source="bar_1.0-1_amd64.deb")
oci_deb_layer(name="layer", debs=[":foo", ":bar"])
""",
        }
    )

    built = package(rule_runner, "layer")
    assert [a.relpath for a in built.artifacts] == ["debs/layer.tar"]

    names = layer_names(rule_runner, built)
    assert "usr/bin/foo" in names
    assert "usr/lib/libbar.so.1.0" in names
    assert "var/lib/dpkg/status.d/foo" in names
    assert "var/lib/dpkg/status.d/bar" in names


def test_rejects_non_deb_files(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "debs/notes.txt": "not a deb",
            "debs/BUILD": """
file(name="notes", source="notes.txt")
oci_deb_layer(name="layer", debs=[":notes"])
""",
        }
    )

    with engine_error(contains="must only contain `.deb` files, got: debs/notes.txt"):
        package(rule_runner, "layer")


def test_merged_usr_can_be_disabled(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "debs/tool_1.0-1_amd64.deb": make_deb("tool", [_dir("./bin/"), ("./bin/tool", b"tool")]),
            "debs/BUILD": """
file(name="tool", source="tool_1.0-1_amd64.deb")
oci_deb_layer(name="merged", debs=[":tool"])
oci_deb_layer(name="split", debs=[":tool"], merged_usr=False)
""",
        }
    )

    assert "usr/bin/tool" in layer_names(rule_runner, package(rule_runner, "merged"))
    assert "bin/tool" in layer_names(rule_runner, package(rule_runner, "split"))


def test_rejects_non_file_targets(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "debs/foo_1.0-1_amd64.deb": FOO,
            "debs/BUILD": """
resource(name="foo", source="foo_1.0-1_amd64.deb")
oci_deb_layer(name="layer", debs=[":foo"])
""",
        }
    )

    with engine_error(contains="must only contain `file` targets, got: debs:foo"):
        package(rule_runner, "layer")


def test_deb_layer_is_added_as_its_own_layer(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "debs/foo_1.0-1_amd64.deb": FOO,
            "debs/BUILD": """
file(name="foo", source="foo_1.0-1_amd64.deb")
oci_deb_layer(name="layer", debs=[":foo"])
""",
        }
    )
    target = rule_runner.get_target(Address("debs", target_name="layer"))

    for old_style in (True, False):
        image_layer = rule_runner.request(layer.ImageLayer, [layer.ImageLayerRequest(target, old_style)])

        # The layer tarball is added as-is, not re-archived as a file inside another layer.
        assert image_layer.layer_command[-1] == "debs/layer.tar"
        assert not image_layer.compressed
        assert "--config.entrypoint" not in image_layer.config_command
