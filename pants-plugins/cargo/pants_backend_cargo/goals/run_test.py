"""`run` of a binary in a workspace: the binary, its closure's `files` and `env`, nothing else."""

from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.core.goals.run import RunRequest
from pants.engine.fs import Digest, DigestContents
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants.util.frozendict import FrozenDict
from pants_backend_cargo.goals.package import CargoBinaryFieldSet
from pants_backend_cargo.testing import make_rule_runner, package_fixture


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(RunRequest, [CargoBinaryFieldSet]),
            QueryRule(DigestContents, [Digest]),
        ]
    )


def test_run_request_holds_the_binary_and_its_closure(rule_runner: RuleRunner) -> None:
    files = {
        "ws/Cargo.toml": '[workspace]\nresolver = "2"\nmembers = ["hello", "other"]\n',
        "ws/Cargo.lock": (
            "version = 4\n\n"
            '[[package]]\nname = "hello"\nversion = "0.1.0"\n\n'
            '[[package]]\nname = "other"\nversion = "0.1.0"\n'
        ),
        "ws/BUILD": 'cargo_workspace(name="workspace")',
        "data/BUILD": (
            'files(name="greeting", sources=["greeting.txt"])\n'
            'files(name="unrelated", sources=["unrelated.txt"])'
        ),
        "data/greeting.txt": "hello\n",
        "data/unrelated.txt": "",
    }
    files.update({f"ws/{k}": v for k, v in package_fixture("hello", main="fn main() {}").items()})
    files.update({f"ws/{k}": v for k, v in package_fixture("other").items()})
    files["ws/hello/BUILD"] = 'cargo_package(dependencies=["//data:greeting"], env=["WHO=world"])'
    files["ws/other/BUILD"] = 'cargo_package(dependencies=["//data:unrelated"], env=["NOT=me"])'
    rule_runner.write_files(files)

    tgt = rule_runner.get_target(Address("ws/hello", generated_name="hello"))
    request = rule_runner.request(RunRequest, [CargoBinaryFieldSet.create(tgt)])
    contents = rule_runner.request(DigestContents, [request.digest])
    assert request.args == ("{chroot}/ws.hello/hello",)
    assert sorted(fc.path for fc in contents) == ["data/greeting.txt", "ws.hello/hello"]
    assert request.extra_env == FrozenDict({"WHO": "world"})
