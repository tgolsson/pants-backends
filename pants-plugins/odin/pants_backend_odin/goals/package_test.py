from __future__ import annotations

from pants.core.goals.package import rules as core_package_rules
from pants.core.util_rules import config_files, external_tool, source_files
from pants.engine.addresses import Address
from pants.engine.internals import graph
from pants.testutil.rule_runner import RuleRunner
from pants_backend_odin import target_types as odin_target_types
from pants_backend_odin.goals.package import OdinBuildRequest, OdinBuildResult, OdinPackageFieldSet
from pants_backend_odin.goals.package import rules as odin_package_rules
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import (
    OdinPackageTarget,
    OdinSourcesGeneratorTarget,
    OdinSourceTarget,
)


def test_odin_package_field_set():
    """Test that OdinPackageFieldSet is properly created from target."""
    rule_runner = RuleRunner(
        rules=[
            *core_package_rules(),
            *odin_package_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *graph.rules(),
            *OdinTool.rules(),
        ],
        target_types=[OdinPackageTarget, OdinSourceTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_package(
    name="main",
    defines=["DEBUG=true", "VERSION=1.0.0"],
    dependencies=[":sources"]
)
""",
            "src/main.odin": "package main\n\nmain :: proc() {\n}",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="main"))
    field_set = OdinPackageFieldSet.create(target)

    assert field_set is not None
    assert field_set.dependencies is not None
    assert field_set.defines is not None
    assert list(field_set.defines.value) == ["DEBUG=true", "VERSION=1.0.0"]


def test_odin_build_request_creation():
    """Test that OdinBuildRequest can be created with proper fields."""
    from pants.engine.fs import EMPTY_DIGEST

    request = OdinBuildRequest(
        address="src:main",
        sources_digest=EMPTY_DIGEST,
        defines=("DEBUG=true", "VERSION=1.0.0"),
        directory="src",
        output_path="src/main",
        mode="exe",
    )

    assert request.address == "src:main"
    assert request.sources_digest == EMPTY_DIGEST
    assert request.defines == ("DEBUG=true", "VERSION=1.0.0")
    assert request.directory == "src"
    assert request.mode == "exe"


def test_odin_build_request_test_mode():
    """Test that OdinBuildRequest can be created with test mode."""
    from pants.engine.fs import EMPTY_DIGEST

    request = OdinBuildRequest(
        address="src:test",
        sources_digest=EMPTY_DIGEST,
        defines=("DEBUG=true",),
        directory="src",
        output_path="src/test",
        mode="test",
    )

    assert request.address == "src:test"
    assert request.mode == "test"


def test_odin_build_result_creation():
    """Test that OdinBuildResult can be created."""
    from pants.engine.fs import EMPTY_DIGEST

    result = OdinBuildResult(
        digest=EMPTY_DIGEST,
        success=True,
    )

    assert result.digest == EMPTY_DIGEST
    assert result.success is True


def test_odin_package_field_set_no_defines():
    """Test OdinPackageFieldSet works without defines field."""
    rule_runner = RuleRunner(
        rules=[
            *core_package_rules(),
            *odin_package_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *graph.rules(),
            *OdinTool.rules(),
        ],
        target_types=[OdinPackageTarget, OdinSourceTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_package(
    name="main",
    dependencies=[":sources"]
)
""",
            "src/main.odin": "package main\n\nmain :: proc() {\n}",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="main"))
    field_set = OdinPackageFieldSet.create(target)

    assert field_set is not None
    assert field_set.dependencies is not None
    assert field_set.defines is not None
    assert list(field_set.defines.value or []) == []


def test_odin_package_target_has_defines_field():
    """Test that OdinPackageTarget includes the defines field."""
    from pants_backend_odin.target_types import OdinDefinesField

    assert OdinDefinesField in OdinPackageTarget.core_fields


def test_odin_defines_field_validation():
    """Test OdinDefinesField properties."""
    from pants_backend_odin.target_types import OdinDefinesField

    assert OdinDefinesField.alias == "defines"
    assert "build-time defines" in OdinDefinesField.help.lower()
