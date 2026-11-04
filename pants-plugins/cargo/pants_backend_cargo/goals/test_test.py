"""The test goal end to end: one `cargo test --no-run` per workspace, one process per
executable."""

from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.core.goals.test import TestResult
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.goals.test import CargoTestFieldSet, CargoTestRequest
from pants_backend_cargo.testing import make_rule_runner, package_fixture

PASSING = """
pub fn add(a: u32, b: u32) -> u32 { a + b }

#[cfg(test)]
mod tests {
    #[test]
    fn adds() { assert_eq!(super::add(1, 2), 3); }
}
"""

FAILING = """
#[cfg(test)]
mod tests {
    #[test]
    fn fails() { assert_eq!(1, 2); }
}
"""


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner([QueryRule(TestResult, [CargoTestRequest.Batch])])


def _run(rule_runner: RuleRunner, address: Address) -> TestResult:
    tgt = rule_runner.get_target(address)
    return rule_runner.request(
        TestResult, [CargoTestRequest.Batch("", (CargoTestFieldSet.create(tgt),), None)]
    )


def test_library_unit_tests(rule_runner: RuleRunner) -> None:
    files = package_fixture("good", lib=PASSING)
    files.update(package_fixture("bad", lib=FAILING))
    rule_runner.write_files(files)

    good = _run(rule_runner, Address("good", generated_name="library"))
    assert good.exit_code == 0, good.stderr_bytes.decode()
    assert "test tests::adds ... ok" in good.stdout_bytes.decode()

    bad = _run(rule_runner, Address("bad", generated_name="library"))
    assert bad.exit_code != 0
    assert "test tests::fails ... FAILED" in bad.stdout_bytes.decode()


def test_integration_test_and_binary(rule_runner: RuleRunner) -> None:
    files = package_fixture("pkg", lib=PASSING, main='fn main() { println!("{}", pkg::add(1, 1)); }')
    files["pkg/tests/it.rs"] = "#[test]\nfn uses_lib() { assert_eq!(pkg::add(2, 2), 4); }\n"
    rule_runner.write_files(files)

    integration = _run(rule_runner, Address("pkg", generated_name="it"))
    assert integration.exit_code == 0, integration.stderr_bytes.decode()
    assert "test uses_lib ... ok" in integration.stdout_bytes.decode()

    binary = _run(rule_runner, Address("pkg", generated_name="pkg"))
    assert binary.exit_code == 0, binary.stderr_bytes.decode()
    assert "running 0 tests" in binary.stdout_bytes.decode()


def test_workspace_members_share_one_build(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "ws/Cargo.toml": '[workspace]\nresolver = "2"\nmembers = ["a", "b"]\n',
            "ws/Cargo.lock": (
                "version = 4\n\n"
                '[[package]]\nname = "a"\nversion = "0.1.0"\n\n'
                '[[package]]\nname = "b"\nversion = "0.1.0"\n'
            ),
            "ws/BUILD": 'cargo_workspace(name="workspace")',
            **{k.replace("a/", "ws/a/", 1): v for k, v in package_fixture("a", lib=PASSING).items()},
            **{k.replace("b/", "ws/b/", 1): v for k, v in package_fixture("b", lib=FAILING).items()},
        }
    )
    a = _run(rule_runner, Address("ws/a", generated_name="library"))
    assert a.exit_code == 0, a.stderr_bytes.decode()
    b = _run(rule_runner, Address("ws/b", generated_name="library"))
    assert b.exit_code != 0
    assert "test tests::fails ... FAILED" in b.stdout_bytes.decode()


def test_skip_tests(rule_runner: RuleRunner) -> None:
    files = package_fixture("skipped", lib=FAILING)
    files["skipped/BUILD"] = "cargo_package(skip_tests=True)"
    rule_runner.write_files(files)
    tgt = rule_runner.get_target(Address("skipped", generated_name="library"))
    assert not CargoTestFieldSet.is_applicable(tgt)


def test_untestable_targets_are_skipped(rule_runner: RuleRunner) -> None:
    files = package_fixture("opt", lib=FAILING)
    files["opt/Cargo.toml"] += (
        "\n[lib]\ntest = false\n\n[features]\nextra = []\n\n"
        '[[test]]\nname = "needs_extra"\nrequired-features = ["extra"]\n'
    )
    files["opt/tests/needs_extra.rs"] = "#[test]\nfn fails() { assert_eq!(1, 2); }\n"
    rule_runner.write_files(files)

    # `test = false`: no test target.
    assert not CargoTestFieldSet.is_applicable(
        rule_runner.get_target(Address("opt", generated_name="library"))
    )
    # `required-features` off: cargo builds no executable at all for the package, which is a
    # skip, not an error.
    skipped = _run(rule_runner, Address("opt", generated_name="needs_extra"))
    assert skipped.exit_code is None


def test_executables_get_the_build_env(rule_runner: RuleRunner) -> None:
    files = package_fixture("dep")
    files["dep/BUILD"] = 'cargo_package(env=["DEP_GREETING=hi"])'
    files.update(
        package_fixture(
            "user",
            lib="""
#[test]
fn sees_env() {
    assert_eq!(std::env::var("DEP_GREETING").unwrap(), "hi");
    let dir = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    assert!(dir.ends_with("user"));
    assert!(dir.join("Cargo.toml").is_file());
}
""",
        )
    )
    files["user/Cargo.toml"] += '\n[dependencies]\ndep = { path = "../dep" }\n'
    files["user/Cargo.lock"] = (
        "version = 4\n\n"
        '[[package]]\nname = "dep"\nversion = "0.1.0"\n\n'
        '[[package]]\nname = "user"\nversion = "0.1.0"\ndependencies = [\n "dep",\n]\n'
    )
    rule_runner.write_files(files)

    result = _run(rule_runner, Address("user", generated_name="library"))
    assert result.exit_code == 0, result.stdout_bytes.decode() + result.stderr_bytes.decode()
    assert "test sees_env ... ok" in result.stdout_bytes.decode()
