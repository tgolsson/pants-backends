from __future__ import annotations

import pytest

from pants.core.target_types import ResourceTarget
from pants.engine.addresses import Address
from pants.testutil.rule_runner import QueryRule, RuleRunner

import pants_backend_odin.util_rules.sandbox
from pants_backend_odin.target_types import OdinSourceTarget
from pants_backend_odin.util_rules.sandbox import (
    PrepareOdinSandboxRequest,
    PrepareOdinSandboxResult,
)


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pants_backend_odin.util_rules.sandbox.rules(),
            QueryRule(PrepareOdinSandboxResult, [PrepareOdinSandboxRequest]),
        ],
        target_types=[OdinSourceTarget, ResourceTarget],
    )


def test_prepare_odin_sandbox_with_sources_only(rule_runner: RuleRunner) -> None:
    """Test preparing sandbox with only Odin source files."""
    rule_runner.write_files({
        "BUILD": """
odin_source(name="main", source="main.odin")
        """,
        "main.odin": """
package main

import "core:fmt"

main :: proc() {
    fmt.println("Hello, World!")
}
        """,
    })
    
    result = rule_runner.request(
        PrepareOdinSandboxResult,
        [PrepareOdinSandboxRequest(Address("", target_name="main"))],
    )
    
    assert result.directory == "."
    assert "main.odin" in result.source_files
    assert len(result.resource_files) == 0


def test_prepare_odin_sandbox_with_sources_and_resources(rule_runner: RuleRunner) -> None:
    """Test preparing sandbox with both source and resource files."""
    rule_runner.write_files({
        "BUILD": """
odin_source(name="main", source="main.odin", dependencies=[":config"])
resource(name="config", source="config.txt")
        """,
        "main.odin": """
package main

import "core:fmt"

main :: proc() {
    fmt.println("Hello, World!")
}
        """,
        "config.txt": "debug=true\n",
    })
    
    result = rule_runner.request(
        PrepareOdinSandboxResult,
        [PrepareOdinSandboxRequest(Address("", target_name="main"))],
    )
    
    assert result.directory == "."
    assert "main.odin" in result.source_files
    assert "config.txt" in result.resource_files


def test_prepare_odin_sandbox_invalid_directory(rule_runner: RuleRunner) -> None:
    """Test that invalid directory paths are rejected."""
    rule_runner.write_files({
        "../BUILD": """
odin_source(name="main", source="main.odin")
        """,
        "../main.odin": "package main",
    })
    
    with pytest.raises(Exception, match="Invalid directory path"):
        rule_runner.request(
            PrepareOdinSandboxResult,
            [PrepareOdinSandboxRequest(Address("../", target_name="main"))],
        )


def test_prepare_odin_sandbox_subdirectory(rule_runner: RuleRunner) -> None:
    """Test preparing sandbox with files in a subdirectory."""
    rule_runner.write_files({
        "src/BUILD": """
odin_source(name="lib", source="lib.odin")
        """,
        "src/lib.odin": """
package lib

add :: proc(a, b: int) -> int {
    return a + b
}
        """,
    })
    
    result = rule_runner.request(
        PrepareOdinSandboxResult,
        [PrepareOdinSandboxRequest(Address("src", target_name="lib"))],
    )
    
    assert result.directory == "src"
    assert "src/lib.odin" in result.source_files
    assert len(result.resource_files) == 0