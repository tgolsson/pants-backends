from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.engine.fs import Digest, DigestContents
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.goals.generate_lockfiles import (
    GenerateCargoLockfileRequest,
    GenerateLockfileResult,
)
from pants_backend_cargo.testing import make_rule_runner


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(GenerateLockfileResult, [GenerateCargoLockfileRequest]),
            QueryRule(DigestContents, [Digest]),
        ]
    )


def test_lock_workspace(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "ws/Cargo.toml": (
                '[workspace]\nmembers = []\n[package]\nname = "foobar"\nversion = "0.1.0"\nedition = "2021"\n'
            ),
            "ws/BUILD": 'cargo_workspace(name="workspace")\ncargo_package()',
            "ws/src/lib.rs": "",
        }
    )
    ws = Address("ws", target_name="workspace")
    result = rule_runner.request(
        GenerateLockfileResult,
        [
            GenerateCargoLockfileRequest(
                root=ws, resolve_name=str(ws), lockfile_dest="ws/Cargo.lock", diff=False
            )
        ],
    )
    assert result.resolve_name == "ws:workspace"
    assert result.path == "ws/Cargo.lock"
    contents = rule_runner.request(DigestContents, [result.digest])
    assert [fc.path for fc in contents] == ["ws/Cargo.lock"]
    assert b'name = "foobar"' in contents[0].content
