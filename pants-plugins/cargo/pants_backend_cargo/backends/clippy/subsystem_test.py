from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.core.goals.lint import LintResult, Partitions
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.backends.clippy.goals.lint import CargoClippyFieldSet, CargoClippyRequest
from pants_backend_cargo.goals.fmt import RootMetadata
from pants_backend_cargo.testing import make_rule_runner, package_fixture


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(Partitions, [CargoClippyRequest.PartitionRequest]),
            QueryRule(LintResult, [CargoClippyRequest.Batch]),
        ]
    )


def _lint(rule_runner: RuleRunner, main: str, *options: str) -> LintResult:
    rule_runner.set_options(["--rustup-rust-version=1.97.0", *options], env_inherit={"PATH"})
    rule_runner.write_files(package_fixture("rust", main=main))
    tgt = rule_runner.get_target(Address("rust", generated_name="package"))
    partitions = rule_runner.request(
        Partitions[CargoClippyFieldSet, RootMetadata],
        [CargoClippyRequest.PartitionRequest((CargoClippyFieldSet.create(tgt),))],
    )
    assert len(partitions) == 1
    partition = partitions[0]
    assert partition.metadata == RootMetadata(tgt.address, workspace=False)
    return rule_runner.request(
        LintResult, [CargoClippyRequest.Batch("", partition.elements, partition.metadata)]
    )


def test_clippy_ok(rule_runner: RuleRunner) -> None:
    result = _lint(rule_runner, 'fn main() { println!("Hello, world!"); }', "--clippy-args=-- -Dwarnings")
    assert result.exit_code == 0, result.stderr


def test_clippy_warns(rule_runner: RuleRunner) -> None:
    result = _lint(rule_runner, "fn main() { let a = 10; }")
    assert result.exit_code == 0
    assert "unused variable: `a`" in result.stderr


def test_clippy_errors(rule_runner: RuleRunner) -> None:
    result = _lint(rule_runner, "fn main() { let a = 10; }", "--clippy-args=-- -Dwarnings")
    assert result.exit_code == 101
    assert "unused variable: `a`" in result.stderr


def test_one_partition_per_workspace(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "ws/Cargo.toml": '[workspace]\nresolver = "2"\nmembers = ["a", "b"]\n',
            "ws/Cargo.lock": "version = 4\n",
            "ws/BUILD": 'cargo_workspace(name="workspace")',
            **{k.replace("a/", "ws/a/", 1): v for k, v in package_fixture("a").items()},
            **{k.replace("b/", "ws/b/", 1): v for k, v in package_fixture("b").items()},
            **package_fixture("loose"),
        }
    )
    field_sets = tuple(
        CargoClippyFieldSet.create(rule_runner.get_target(Address(path, generated_name="package")))
        for path in ("ws/a", "ws/b", "loose")
    )
    partitions = rule_runner.request(
        Partitions[CargoClippyFieldSet, RootMetadata],
        [CargoClippyRequest.PartitionRequest(field_sets)],
    )
    assert sorted(str(p.metadata.root) for p in partitions) == ["loose#package", "ws:workspace"]
