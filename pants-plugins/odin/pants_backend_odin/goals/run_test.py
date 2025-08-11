from __future__ import annotations

from pants.core.goals.package import rules as package_rules
from pants.core.goals.run import rules as run_rules
from pants.core.util_rules import config_files, external_tool, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.internals import graph
from pants.testutil.rule_runner import RuleRunner
from pants_backend_odin import target_types as odin_target_types
from pants_backend_odin.goals.package import rules as odin_package_rules
from pants_backend_odin.goals.run import OdinBinaryRunFieldSet
from pants_backend_odin.goals.run import rules as odin_run_rules
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import (
    OdinBinaryTarget,
    OdinPackageTarget,
    OdinSourcesGeneratorTarget,
    OdinSourceTarget,
)
from pants_backend_odin.util_rules import build


def test_odin_binary_run_field_set():
    """Test that OdinBinaryRunFieldSet is properly created from odin_binary target."""
    rule_runner = RuleRunner(
        rules=[
            *run_rules(),
            *package_rules(),
            *odin_package_rules(),
            *odin_run_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *graph.rules(),
            *build.rules(),
            *OdinTool.rules(),
        ],
        target_types=[
            OdinPackageTarget,
            OdinSourceTarget,
            OdinSourcesGeneratorTarget,
            OdinBinaryTarget,
        ],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_binary(
    name="main",
    output_path="bin/main",
    defines=["DEBUG=true"]
)
""",
            "src/main.odin": "package main\n\nmain :: proc() {\n}",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="main"))
    field_set = OdinBinaryRunFieldSet.create(target)

    assert field_set is not None
    assert field_set.dependencies is not None
    assert field_set.defines is not None
    assert field_set.output_path is not None
    assert field_set.output_path.value == "bin/main"
    assert list(field_set.defines.value) == ["DEBUG=true"]


def test_odin_binary_run_field_set_no_defines():
    """Test OdinBinaryRunFieldSet works without defines field."""
    rule_runner = RuleRunner(
        rules=[
            *run_rules(),
            *package_rules(),
            *odin_package_rules(),
            *odin_run_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *graph.rules(),
            *build.rules(),
            *OdinTool.rules(),
        ],
        target_types=[
            OdinPackageTarget,
            OdinSourceTarget,
            OdinSourcesGeneratorTarget,
            OdinBinaryTarget,
        ],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_binary(name="main")
""",
            "src/main.odin": "package main\n\nmain :: proc() {\n}",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="main"))
    field_set = OdinBinaryRunFieldSet.create(target)

    assert field_set is not None
    assert field_set.dependencies is not None
    assert field_set.defines is not None
    assert list(field_set.defines.value or []) == []


def test_odin_binary_target_has_required_fields():
    """Test that OdinBinaryTarget has all required fields for run support."""
    from pants.core.goals.package import OutputPathField
    from pants_backend_odin.target_types import OdinDefinesField, OdinDependenciesField

    assert OdinDependenciesField in OdinBinaryTarget.core_fields
    assert OdinDefinesField in OdinBinaryTarget.core_fields
    assert OutputPathField in OdinBinaryTarget.core_fields


def test_odin_binary_run_field_set_requirements():
    """Test that OdinBinaryRunFieldSet has correct required fields."""
    from pants.core.goals.package import OutputPathField
    from pants_backend_odin.target_types import OdinDependenciesField

    required_fields = OdinBinaryRunFieldSet.required_fields

    assert OdinDependenciesField in required_fields
    assert OutputPathField in required_fields
