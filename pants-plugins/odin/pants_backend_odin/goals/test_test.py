from __future__ import annotations

from pants.core.goals.test import rules as core_test_rules
from pants.core.util_rules import config_files, external_tool, source_files
from pants.engine.addresses import Address
from pants.engine.internals import graph
from pants.testutil.rule_runner import RuleRunner
from pants_backend_odin import target_types as odin_target_types
from pants_backend_odin.goals.package import rules as odin_package_rules
from pants_backend_odin.goals.test import OdinTestFieldSet, OdinTestRequest
from pants_backend_odin.goals.test import rules as odin_test_rules
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import (
    OdinSourcesGeneratorTarget,
    OdinSourceTarget,
    OdinTestTarget,
)


def test_odin_test_field_set():
    """Test that OdinTestFieldSet is properly created from target."""
    rule_runner = RuleRunner(
        rules=[
            *core_test_rules(),
            *odin_test_rules(),
            *odin_package_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *graph.rules(),
            *OdinTool.rules(),
        ],
        target_types=[OdinTestTarget, OdinSourceTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_test(
    name="mytest",
    defines=["DEBUG=true", "VERSION=1.0.0"],
    dependencies=[":sources"]
)
""",
            "src/main_test.odin": """package main

import "core:testing"

@(test)
test_square :: proc(t: ^testing.T) {
    result := square(4)
    testing.expect_value(t, result, 16)
}

square :: proc(x: int) -> int {
    return x * x
}
""",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="mytest"))
    field_set = OdinTestFieldSet.create(target)

    assert field_set is not None
    assert field_set.dependencies is not None
    assert field_set.defines is not None
    assert list(field_set.defines.value) == ["DEBUG=true", "VERSION=1.0.0"]


def test_odin_test_request_creation():
    """Test that OdinTestRequest can be created with proper field set."""
    rule_runner = RuleRunner(
        rules=[
            *core_test_rules(),
            *odin_test_rules(),
            *odin_package_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *graph.rules(),
            *OdinTool.rules(),
        ],
        target_types=[OdinTestTarget, OdinSourceTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_test(
    name="mytest",
    dependencies=[":sources"]
)
""",
            "src/main_test.odin": """package main

import "core:testing"

@(test)
test_example :: proc(t: ^testing.T) {
    testing.expect_value(t, 1, 1)
}
""",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="mytest"))
    field_set = OdinTestFieldSet.create(target)
    request = OdinTestRequest.Batch("", (field_set,), None)

    assert request is not None
    assert len(request.elements) == 1
    assert request.elements[0] == field_set


def test_odin_test_field_set_no_defines():
    """Test OdinTestFieldSet works without defines field."""
    rule_runner = RuleRunner(
        rules=[
            *core_test_rules(),
            *odin_test_rules(),
            *odin_package_rules(),
            *odin_target_types.rules(),
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *graph.rules(),
            *OdinTool.rules(),
        ],
        target_types=[OdinTestTarget, OdinSourceTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/BUILD": """
odin_sources(name="sources")
odin_test(
    name="mytest",
    dependencies=[":sources"]
)
""",
            "src/main_test.odin": """package main

import "core:testing"

@(test)
test_example :: proc(t: ^testing.T) {
    testing.expect_value(t, 1, 1)
}
""",
        }
    )

    target = rule_runner.get_target(Address("src", target_name="mytest"))
    field_set = OdinTestFieldSet.create(target)

    assert field_set is not None
    assert field_set.dependencies is not None
    assert field_set.defines is not None
    assert list(field_set.defines.value or []) == []


def test_odin_test_target_has_defines_field():
    """Test that OdinTestTarget includes the defines field."""
    from pants_backend_odin.target_types import OdinDefinesField

    assert OdinDefinesField in OdinTestTarget.core_fields


def test_odin_test_target_alias():
    """Test that OdinTestTarget has the correct alias."""
    assert OdinTestTarget.alias == "odin_test"