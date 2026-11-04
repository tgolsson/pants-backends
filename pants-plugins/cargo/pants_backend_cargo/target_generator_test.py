"""Generated target names: one per cargo target, and none colliding."""

from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.engine.internals.scheduler import ExecutionError
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.target_types import CargoPackageNameField, SkipCargoTestsField
from pants_backend_cargo.testing import make_rule_runner, package_fixture


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner()


def test_test_false_skips_tests(rule_runner: RuleRunner) -> None:
    files = package_fixture("pkg", main="fn main() {}")
    files["pkg/Cargo.toml"] += '\n[[bin]]\nname = "pkg"\npath = "src/main.rs"\ntest = false\n'
    rule_runner.write_files(files)
    assert rule_runner.get_target(Address("pkg", generated_name="pkg"))[SkipCargoTestsField].value
    library = rule_runner.get_target(Address("pkg", generated_name="library"))
    assert not library[SkipCargoTestsField].value


@pytest.mark.parametrize(
    "extra, message",
    [
        (
            '[[bin]]\nname = "package"\npath = "src/main.rs"\n',
            r"pkg: the bin target `package` would be generated as `#package`, which is "
            r"reserved",
        ),
        (
            '[[test]]\nname = "library"\npath = "tests/it.rs"\n',
            r"pkg: the test target `library` would be generated as `#library`, which is "
            r"reserved",
        ),
        (
            ('[[bin]]\nname = "dup"\npath = "src/main.rs"\n\n[[test]]\nname = "dup"\npath = "tests/it.rs"\n'),
            r"pkg: the bin target and the test target `dup` would both be generated as " r"`#dup`",
        ),
    ],
)
def test_generated_name_collisions(rule_runner: RuleRunner, extra: str, message: str) -> None:
    files = package_fixture("pkg", main="fn main() {}")
    files["pkg/Cargo.toml"] += f"\n{extra}"
    files["pkg/tests/it.rs"] = ""
    rule_runner.write_files(files)
    with pytest.raises(ExecutionError, match=message):
        rule_runner.get_target(Address("pkg", generated_name="library"))


def test_a_workspace_only_manifest_is_not_a_package(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "ws/BUILD": "cargo_package()",
            "ws/Cargo.toml": "[workspace]\nmembers = []\n",
        }
    )
    with pytest.raises(ExecutionError, match=r"ws: its Cargo.toml has no `\[package\]`"):
        rule_runner.get_target(Address("ws", generated_name="package"))


def test_workspace_root_package_is_its_own(rule_runner: RuleRunner) -> None:
    # The member is in the root's sources so `cargo metadata` can load it, and comes first
    # in `members`.
    rule_runner.write_files(
        {
            "ws/BUILD": 'cargo_package(sources=["Cargo.toml", "Cargo.lock", "src/*", "a/**/*"])',
            "ws/Cargo.toml": (
                '[package]\nname = "root"\nversion = "0.1.0"\nedition = "2021"\n\n'
                '[workspace]\nmembers = ["a"]\n'
            ),
            "ws/Cargo.lock": (
                'version = 4\n\n[[package]]\nname = "a"\nversion = "0.1.0"\n\n'
                '[[package]]\nname = "root"\nversion = "0.1.0"\n'
            ),
            "ws/src/lib.rs": "",
            "ws/a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n',
            "ws/a/src/lib.rs": "",
        }
    )
    package = rule_runner.get_target(Address("ws", generated_name="package"))
    assert package[CargoPackageNameField].value == "root"
