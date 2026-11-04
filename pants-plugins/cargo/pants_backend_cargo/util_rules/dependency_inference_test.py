from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.engine.rules import QueryRule
from pants.engine.target import InferredDependencies
from pants.testutil.rule_runner import RuleRunner
from pants.util.ordered_set import FrozenOrderedSet
from pants_backend_cargo.testing import make_rule_runner, package_fixture
from pants_backend_cargo.util_rules import dependency_inference


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner([QueryRule(InferredDependencies, [dependency_inference.InferCargoDependencies])])


def _infer(rule_runner: RuleRunner, address: Address) -> InferredDependencies:
    tgt = rule_runner.get_target(address)
    return rule_runner.request(
        InferredDependencies,
        [
            dependency_inference.InferCargoDependencies(
                dependency_inference.CargoDependenciesInferenceFieldSet.create(tgt)
            )
        ],
    )


def test_infer_path_dependency(rule_runner: RuleRunner) -> None:
    files = package_fixture("outer")
    files["outer/Cargo.toml"] += '\n[dependencies]\ninner = { path = "inner" }\n'
    files["outer/Cargo.lock"] = (
        "version = 4\n\n"
        '[[package]]\nname = "inner"\nversion = "0.1.0"\n\n'
        '[[package]]\nname = "outer"\nversion = "0.1.0"\ndependencies = ["inner"]\n'
    )
    files.update({k.replace("inner/", "outer/inner/", 1): v for k, v in package_fixture("inner").items()})
    rule_runner.write_files(files)

    assert _infer(rule_runner, Address("outer", generated_name="package")) == InferredDependencies(
        FrozenOrderedSet([Address("outer/inner", generated_name="package")])
    )


def test_member_depends_on_workspace(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "ws/Cargo.toml": '[workspace]\nresolver = "2"\nmembers = ["a"]\n',
            "ws/Cargo.lock": "version = 4\n",
            "ws/BUILD": 'cargo_workspace(name="workspace")',
            **{k.replace("a/", "ws/a/", 1): v for k, v in package_fixture("a").items()},
        }
    )
    assert _infer(rule_runner, Address("ws/a", generated_name="package")) == InferredDependencies(
        FrozenOrderedSet([Address("ws", target_name="workspace")])
    )


def test_infer_platform_specific_path_dependencies(rule_runner: RuleRunner) -> None:
    files = {}
    for name in ("a", "b", "c"):
        files.update(package_fixture(name))
    files["a/Cargo.toml"] += (
        "\n[target.'cfg(unix)'.dependencies]\nb = { path = \"../b\" }\n"
        "\n[target.'cfg(windows)'.build-dependencies]\nc = { path = \"../c\" }\n"
    )
    rule_runner.write_files(files)
    assert _infer(rule_runner, Address("a", generated_name="package")) == InferredDependencies(
        FrozenOrderedSet(
            [
                Address("b", generated_name="package"),
                Address("c", generated_name="package"),
            ]
        )
    )
