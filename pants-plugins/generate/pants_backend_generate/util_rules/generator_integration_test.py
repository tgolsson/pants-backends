from __future__ import annotations

import logging

import pytest
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals.lint import LintTargetsRequest
from pants.core.target_types import FileSourceField
from pants.core.util_rules import source_files
from pants.engine.addresses import Address
from pants.engine.fs import CreateDigest, DigestContents, FileContent, Snapshot
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.intrinsics import create_digest, digest_to_snapshot, get_digest_contents
from pants.engine.rules import QueryRule, rule
from pants.engine.target import HydratedSources, HydrateSourcesRequest
from pants.engine.unions import UnionMembership, UnionRule
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_generate import specialization
from pants_backend_generate.file_generate import file_generate
from pants_backend_generate.goals.generate import Generate
from pants_backend_generate.goals.lint import GenerateLintRequest
from pants_backend_generate.specialization import GenerateSpecialization
from pants_backend_generate.testutil import generate_build, hydrate, rule_runner, write_project
from pants_backend_generate.util_rules.formatting import FormatOutputsRequest, FormattedOutputs

GEN = Address("proj", target_name="gen")


@pytest.fixture
def runner() -> PythonRuleRunner:
    return rule_runner()


def _contents(runner: PythonRuleRunner, digest) -> dict[str, str]:
    return {fc.path: fc.content.decode() for fc in runner.request(DigestContents, [digest])}


def test_file_generate(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build()})
    generated = hydrate(runner, GEN)
    assert _contents(runner, generated.snapshot.digest) == {
        "proj/out/a.txt": "a.txt: hello\n",
        "proj/out/b.txt": "b.txt: hello\n",
    }


def test_extra_output(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build(args='"--extra"')})
    with pytest.raises(ExecutionError, match=r"(?s)not declared in `outputs`.*proj/out/c\.txt"):
        hydrate(runner, GEN)


def test_missing_output(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build(args='"--missing"')})
    with pytest.raises(ExecutionError, match=r"(?s)Declared in `outputs` but not written.*b\.txt"):
        hydrate(runner, GEN)


def test_output_that_is_an_input(runner: PythonRuleRunner) -> None:
    build = generate_build(outputs='"proj/out/a.txt", "proj/out/b.txt", "proj/out/notes.md"')
    write_project(runner, {"proj/BUILD": build})
    with pytest.raises(ExecutionError, match=r"(?s)also inputs.*proj/out/notes\.md"):
        hydrate(runner, GEN)


def test_overwritten_input(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build(args='"--clobber"')})
    with pytest.raises(ExecutionError, match=r"(?s)these were inputs.*proj/out/notes\.md"):
        hydrate(runner, GEN)


def test_output_directories_are_created(runner: PythonRuleRunner) -> None:
    outputs = '"proj/out/a.txt", "proj/out/b.txt", "proj/new/deep/x.txt"'
    write_project(runner, {"proj/BUILD": generate_build(args='"--nested"', outputs=outputs)})
    generated = hydrate(runner, GEN)
    assert _contents(runner, generated.snapshot.digest)["proj/new/deep/x.txt"] == "x.txt: hello\n"


def test_execution_dependencies(runner: PythonRuleRunner) -> None:
    # In an output directory, yet not an extra.
    build = generate_build(args='"--data"', fields='execution_dependencies=[":data"],')
    build += 'files(name="data", sources=["out/data.txt"])\n'
    write_project(runner, {"proj/BUILD": build, "proj/out/data.txt": "and data\n"})
    generated = hydrate(runner, GEN)
    assert _contents(runner, generated.snapshot.digest) == {
        "proj/out/a.txt": "a.txt: hello and data\n",
        "proj/out/b.txt": "b.txt: hello and data\n",
    }


def test_runnable_dependencies(runner: PythonRuleRunner) -> None:
    build = generate_build(args='"--helper"', fields='runnable_dependencies=["//tools:helper"],')
    write_project(runner, {"proj/BUILD": build})
    generated = hydrate(runner, GEN)
    assert _contents(runner, generated.snapshot.digest)["proj/out/a.txt"] == "a.txt: hello and helper\n"


def test_extra_env_vars(runner: PythonRuleRunner) -> None:
    build = generate_build(fields='extra_env_vars=["GEN_SUFFIX= and env"],')
    write_project(runner, {"proj/BUILD": build})
    generated = hydrate(runner, GEN)
    assert _contents(runner, generated.snapshot.digest)["proj/out/a.txt"] == "a.txt: hello and env\n"


def test_timeout(runner: PythonRuleRunner) -> None:
    write_project(runner, {"proj/BUILD": generate_build(args='"--sleep", "60"', fields="timeout=1,")})
    with pytest.raises(ExecutionError, match=r"Exceeded timeout of 1.0 seconds"):
        hydrate(runner, GEN)


def test_duplicate_alias() -> None:
    with pytest.raises(ValueError, match="'file_generate' already exists"):
        GenerateSpecialization("file_generate", FileSourceField)


@pytest.mark.parametrize(
    "outputs, problem",
    [
        ("", "at least one file"),
        ('""', "must not be empty"),
        ('"."', "files, not directories"),
        ('"x/.."', "files, not directories"),
        ('"a/"', "files, not directories"),
        ('"a/../../x"', "inside the build root"),
        ('"a//b"', "write it as 'a/b'"),
        ('"a/../b"', "write it as 'b'"),
        ('"proj/out/*.txt"', "not globs"),
        ('"../out.txt"', "inside the build root"),
        ('"/abs.txt"', "relative to the build root"),
        ('"proj/./out/a.txt"', "write it as 'proj/out/a.txt'"),
        ('"proj/out/a.txt", "proj/out/a.txt"', "more than once"),
    ],
)
def test_outputs_validation(runner: PythonRuleRunner, outputs: str, problem: str) -> None:
    write_project(runner, {"proj/BUILD": generate_build(outputs=outputs)})
    with pytest.raises(ExecutionError, match=problem):
        runner.get_target(GEN)


class UpperOutputsRequest(FormatOutputsRequest):
    pass


class DropOutputsRequest(FormatOutputsRequest):
    pass


class AddOutputsRequest(FormatOutputsRequest):
    pass


async def _upper(snapshot: Snapshot, drop: bool = False, add: bool = False) -> FormattedOutputs:
    contents = await get_digest_contents(snapshot.digest)
    files = [
        FileContent(fc.path, fc.content.upper())
        for fc in contents
        if not (drop and fc.path.endswith("b.txt"))
    ]
    if add:
        files.append(FileContent("proj/out/added.txt", b"ADDED\n"))
    digest = await create_digest(CreateDigest(files))
    formatted = await digest_to_snapshot(digest)
    return FormattedOutputs(formatted)


@rule
async def upper_outputs(request: UpperOutputsRequest) -> FormattedOutputs:
    return await _upper(request.snapshot)


@rule
async def drop_outputs(request: DropOutputsRequest) -> FormattedOutputs:
    return await _upper(request.snapshot, drop=True)


@rule
async def add_outputs(request: AddOutputsRequest) -> FormattedOutputs:
    return await _upper(request.snapshot, add=True)


upper_generate = GenerateSpecialization("upper_generate", FileSourceField, UpperOutputsRequest)
drop_generate = GenerateSpecialization("drop_generate", FileSourceField, DropOutputsRequest)
add_generate = GenerateSpecialization("add_generate", FileSourceField, AddOutputsRequest)
FORMATTER_SPECS = (upper_generate, drop_generate, add_generate)


def _formatter_runner() -> PythonRuleRunner:
    return rule_runner(
        upper_outputs,
        drop_outputs,
        add_outputs,
        UnionRule(FormatOutputsRequest, UpperOutputsRequest),
        UnionRule(FormatOutputsRequest, DropOutputsRequest),
        UnionRule(FormatOutputsRequest, AddOutputsRequest),
        *(r for spec in FORMATTER_SPECS for r in spec.rules()),
        target_types=[t for spec in FORMATTER_SPECS for t in spec.target_types()],
    )


def test_formatter_runs() -> None:
    runner = _formatter_runner()
    build = generate_build().replace("file_generate", "upper_generate")
    write_project(runner, {"proj/BUILD": build})
    generated = hydrate(runner, GEN)
    assert _contents(runner, generated.snapshot.digest) == {
        "proj/out/a.txt": "A.TXT: HELLO\n",
        "proj/out/b.txt": "B.TXT: HELLO\n",
    }


def test_formatter_must_keep_the_files() -> None:
    runner = _formatter_runner()
    build = generate_build().replace("file_generate", "drop_generate")
    write_project(runner, {"proj/BUILD": build})
    with pytest.raises(ExecutionError, match=r"DropOutputsRequest changed the files"):
        hydrate(runner, GEN)


def test_formatter_must_not_add_files() -> None:
    runner = _formatter_runner()
    build = generate_build().replace("file_generate", "add_generate")
    write_project(runner, {"proj/BUILD": build})
    with pytest.raises(ExecutionError, match=r"(?s)AddOutputsRequest changed the files.*added\.txt"):
        hydrate(runner, GEN)


WARNING = "`pants generate` and the `generate` linter are unavailable"


def test_specialization_without_goal_backend(caplog, monkeypatch) -> None:
    monkeypatch.setattr(specialization, "_warned_without_goal_backend", False)
    runner = PythonRuleRunner(
        rules=[
            *python_register.rules(),
            *core_target_types.rules(),
            *source_files.rules(),
            *file_generate.rules(),
            QueryRule(HydratedSources, [HydrateSourcesRequest]),
        ],
        target_types=[*file_generate.target_types(), *python_register.target_types()],
    )
    write_project(runner, {"proj/BUILD": generate_build()})
    with caplog.at_level(logging.WARNING):
        generated = hydrate(runner, GEN)
    assert set(_contents(runner, generated.snapshot.digest)) == {"proj/out/a.txt", "proj/out/b.txt"}
    assert WARNING in caplog.text

    assert not any(getattr(r, "output_type", None) is Generate for r in runner.build_config.rules)
    lint_requests = runner.request(UnionMembership, []).get(LintTargetsRequest)
    assert GenerateLintRequest not in lint_requests


def test_no_warning_with_goal_backend(runner: PythonRuleRunner, caplog, monkeypatch) -> None:
    monkeypatch.setattr(specialization, "_warned_without_goal_backend", False)
    write_project(runner, {"proj/BUILD": generate_build()}, "--backend-packages=['pants_backend_generate']")
    with caplog.at_level(logging.WARNING):
        hydrate(runner, GEN)
    assert WARNING not in caplog.text
