from __future__ import annotations

import pytest
from pants.core.goals.tailor import AllOwnedSources, PutativeTarget, PutativeTargets
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.goals.tailor import PutativeCargoTargetsRequest
from pants_backend_cargo.target_types import CargoPackageTarget, CargoWorkspaceTarget
from pants_backend_cargo.testing import make_rule_runner


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner([QueryRule(PutativeTargets, [PutativeCargoTargetsRequest, AllOwnedSources])])


def test_find_cargo_package_targets(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "unowned/Cargo.toml": '[package]\nname="foobar"\nversion = "0.1.0"\n',
            "unowned/src/lib.rs": "",
            "owned/Cargo.toml": '[package]\nname="foobar"\nversion = "0.1.0"',
            "owned/BUILD": "cargo_package()",
            "owned/src/lib.rs": "",
        }
    )
    putative_targets = rule_runner.request(
        PutativeTargets,
        [PutativeCargoTargetsRequest(("unowned", "owned")), AllOwnedSources(["owned/Cargo.toml"])],
    )
    assert putative_targets == PutativeTargets(
        [
            PutativeTarget.for_target_type(
                CargoPackageTarget, path="unowned", name=None, triggering_sources=["Cargo.toml"]
            )
        ]
    )


def test_workspace_targets(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "unowned/Cargo.toml": "[workspace]",
            "owned/Cargo.toml": '[package]\nname="foobar"\nversion = "0.1.0"',
            "owned/BUILD": "cargo_package()",
            "owned/src/lib.rs": "",
        }
    )
    putative_targets = rule_runner.request(
        PutativeTargets,
        [PutativeCargoTargetsRequest(("unowned", "owned")), AllOwnedSources(["owned/Cargo.toml"])],
    )
    assert putative_targets == PutativeTargets(
        [
            PutativeTarget.for_target_type(
                CargoWorkspaceTarget,
                path="unowned",
                name="workspace",
                triggering_sources=["Cargo.toml"],
            )
        ]
    )
