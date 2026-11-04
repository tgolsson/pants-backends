"""`cargo fmt` formats the batch's files as the fmt chain hands them over."""

from __future__ import annotations

import pytest
from pants.build_graph.address import Address
from pants.core.goals.fmt import FmtResult
from pants.engine.fs import CreateDigest, Digest, DigestContents, FileContent, Snapshot
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.goals.fmt import CargoFmtRequest, RootMetadata
from pants_backend_cargo.testing import make_rule_runner, package_fixture


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(FmtResult, [CargoFmtRequest.Batch]),
            QueryRule(Digest, [CreateDigest]),
            QueryRule(Snapshot, [Digest]),
            QueryRule(DigestContents, [Digest]),
        ]
    )


def test_a_file_named_with_glob_characters_comes_from_the_batch(rule_runner: RuleRunner) -> None:
    lib = '#[path = "[gen].rs"]\nmod gen;\n'
    files = package_fixture("pkg", lib=lib)
    files["pkg/src/[gen].rs"] = "pub fn gen() -> i32 {   1 }\n"
    rule_runner.write_files(files)
    # An earlier formatter in the chain changed the file, so the batch's copy differs.
    digest = rule_runner.request(
        Digest,
        [
            CreateDigest(
                [
                    FileContent("pkg/src/[gen].rs", b"pub fn gen() -> i32 {   2 }\n"),
                    FileContent("pkg/src/lib.rs", lib.encode()),
                ]
            )
        ],
    )
    batch = CargoFmtRequest.Batch(
        "rustfmt",
        ("pkg/src/[gen].rs", "pkg/src/lib.rs"),
        RootMetadata(Address("pkg", generated_name="package"), workspace=False),
        rule_runner.request(Snapshot, [digest]),
    )
    result = rule_runner.request(FmtResult, [batch])
    contents = rule_runner.request(DigestContents, [result.output.digest])
    by_path = {fc.path: fc.content.decode() for fc in contents}
    assert by_path["pkg/src/[gen].rs"] == "pub fn gen() -> i32 {\n    2\n}\n"
