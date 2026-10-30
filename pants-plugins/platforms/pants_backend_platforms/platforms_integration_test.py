from __future__ import annotations

import logging

import pytest
from pants.core.goals.package import BuiltPackage
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_platforms.testutil import (
    HOST,
    HOST_ARCH,
    HOST_OS,
    OTHER,
    SHADOW_RULES,
    ToyBundleFieldSet,
    ToyFieldSet,
    platforms_build,
    rule_runner,
)
from pants_backend_platforms.util_rules.lookup import TargetsOfAlias, TargetsOfAliasRequest

BOTH = f'platforms=["{HOST}", "{OTHER}"]'


@pytest.fixture
def runner() -> RuleRunner:
    return rule_runner()


def _setup(runner: RuleRunner, files: dict[str, str], *options: str, enabled: tuple[str, ...] = ()) -> None:
    runner.write_files(files)
    runner.set_options([f"--platforms-enabled={list(enabled)!r}", *options], env_inherit={"PATH"})


def _package(runner: RuleRunner, spec_path: str, name: str) -> dict[str, str]:
    target = runner.get_target(Address(spec_path, target_name=name))
    field_set_type = ToyBundleFieldSet if ToyBundleFieldSet.is_applicable(target) else ToyFieldSet
    built = runner.request(BuiltPackage, [field_set_type.create(target)])
    contents = runner.request(DigestContents, [built.digest])
    assert sorted(a.relpath for a in built.artifacts) == sorted(fc.path for fc in contents)
    return {fc.path: fc.content.decode() for fc in contents}


def test_host_only_by_default(runner: RuleRunner) -> None:
    _setup(runner, {"p/BUILD": platforms_build(), "a/BUILD": f'toy_artifact(name="a", text="A", {BOTH})'})
    assert _package(runner, "a", "a") == {"a/a.txt": f"A for {HOST}\n"}


def test_enabled_platform_goes_under_its_name(runner: RuleRunner) -> None:
    _setup(
        runner,
        {"p/BUILD": platforms_build(), "a/BUILD": f'toy_artifact(name="a", text="A", {BOTH})'},
        enabled=(OTHER,),
    )
    assert _package(runner, "a", "a") == {
        "a/a.txt": f"A for {HOST}\n",
        f"{OTHER}/a/a.exe.txt": f"A for {OTHER}\n",
    }


def test_repeated_platforms_build_once(runner: RuleRunner) -> None:
    build = f'toy_artifact(name="a", text="A", platforms=["{OTHER}", "{HOST}", "{OTHER}"])'
    _setup(runner, {"p/BUILD": platforms_build(), "a/BUILD": build}, enabled=(OTHER,))
    assert sorted(_package(runner, "a", "a")) == ["a/a.txt", f"{OTHER}/a/a.exe.txt"]


def test_unset_platforms_is_the_host_even_when_others_are_enabled(runner: RuleRunner) -> None:
    _setup(
        runner,
        {"p/BUILD": platforms_build(), "a/BUILD": 'toy_artifact(name="a", text="A")'},
        enabled=(OTHER,),
    )
    assert _package(runner, "a", "a") == {"a/a.txt": f"A for {HOST}\n"}


def test_nothing_selected(runner: RuleRunner, caplog) -> None:
    build = f'toy_artifact(name="a", text="A", platforms=["{OTHER}"])'
    _setup(runner, {"p/BUILD": platforms_build(), "a/BUILD": build})
    with caplog.at_level(logging.INFO):
        assert _package(runner, "a", "a") == {}
    assert "nothing is packaged" in caplog.text


def test_dependencies_follow_the_platform_being_built(runner: RuleRunner) -> None:
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "lib/BUILD": f'toy_artifact(name="lib", text="L", {BOTH})',
            "a/BUILD": f'toy_artifact(name="a", text="A", dependencies=["//lib"], {BOTH})',
        },
        enabled=(OTHER,),
    )
    assert _package(runner, "a", "a")[f"{OTHER}/a/a.exe.txt"] == f"A for {OTHER}\nL for {OTHER}\n"


def test_dependencies_across_member_types(runner: RuleRunner) -> None:
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "lib/BUILD": f'toy_artifact(name="lib", text="L", {BOTH})',
            "b/BUILD": f'toy_bundle(name="b", dependencies=["//lib"], {BOTH})',
            "a/BUILD": f'toy_artifact(name="a", text="A", dependencies=["//b"], {BOTH})',
        },
        enabled=(OTHER,),
    )
    assert _package(runner, "a", "a")[f"{OTHER}/a/a.exe.txt"] == (
        f"A for {OTHER}\nbundle for {OTHER}\nL for {OTHER}\n"
    )


def test_dependency_that_does_not_list_the_platform(runner: RuleRunner) -> None:
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "lib/BUILD": 'toy_artifact(name="lib", text="L")',
            "a/BUILD": f'toy_artifact(name="a", text="A", dependencies=["//lib"], {BOTH})',
        },
        enabled=(OTHER,),
    )
    with pytest.raises(
        ExecutionError,
        match=rf"a:a is being built for {OTHER}, but its dependency lib:lib lists only \['{HOST}'\]",
    ):
        _package(runner, "a", "a")


def test_dependency_with_an_unknown_platform(runner: RuleRunner) -> None:
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "lib/BUILD": 'toy_artifact(name="lib", text="L", platforms=["windos"])',
            "a/BUILD": 'toy_artifact(name="a", text="A", dependencies=["//lib"])',
        },
    )
    with pytest.raises(ExecutionError, match=r"the `platforms` field of lib:lib: unknown platform `windos`"):
        _package(runner, "a", "a")


def test_dependency_with_two_members(runner: RuleRunner) -> None:
    runner = rule_runner(*SHADOW_RULES)
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "lib/BUILD": 'toy_artifact(name="lib", text="L")',
            "a/BUILD": 'toy_bundle(name="a", dependencies=["//lib"])',
        },
    )
    with pytest.raises(ExecutionError, match=r"lib:lib matches several platform build field sets"):
        _package(runner, "a", "a")


def test_tool_is_built_for_the_host(runner: RuleRunner) -> None:
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "a/BUILD": 'toy_artifact(name="gen", text="G")\n'
            f'toy_artifact(name="a", text="A", tool="gen", {BOTH})',
        },
        enabled=(OTHER,),
    )
    assert _package(runner, "a", "a")[f"{OTHER}/a/a.exe.txt"] == f"A for {OTHER}\nG for {HOST}\n"


def test_unknown_platform_in_field(runner: RuleRunner) -> None:
    _setup(
        runner,
        {"p/BUILD": platforms_build(), "a/BUILD": 'toy_artifact(name="a", text="A", platforms=["nope"])'},
    )
    with pytest.raises(
        ExecutionError, match=r"the `platforms` field of a:a: unknown platform `nope`; known: \["
    ):
        _package(runner, "a", "a")


def test_unknown_platform_in_option(runner: RuleRunner) -> None:
    _setup(
        runner,
        {"p/BUILD": platforms_build(), "a/BUILD": 'toy_artifact(name="a", text="A")'},
        enabled=("nope",),
    )
    with pytest.raises(ExecutionError, match=r"\[platforms\]\.enabled: unknown platform `nope`"):
        _package(runner, "a", "a")


def test_duplicate_platform_names(runner: RuleRunner) -> None:
    _setup(
        runner,
        {
            "p/BUILD": platforms_build(),
            "q/BUILD": f'platform(name="{OTHER}", os="windows", arch="x86_64")',
            "a/BUILD": 'toy_artifact(name="a", text="A")',
        },
    )
    with pytest.raises(ExecutionError, match=rf"Two `platform` targets are named `{OTHER}`"):
        _package(runner, "a", "a")


SECOND_HOST = f'platform(name="musl", os="{HOST_OS}", arch="{HOST_ARCH}")\n'


def test_several_platforms_match_the_host(runner: RuleRunner) -> None:
    _setup(
        runner, {"p/BUILD": platforms_build() + SECOND_HOST, "a/BUILD": 'toy_artifact(name="a", text="A")'}
    )
    with pytest.raises(
        ExecutionError, match=r"Several `platform` targets match this machine.*\[platforms\]\.host"
    ):
        _package(runner, "a", "a")


def test_host_option_chooses(runner: RuleRunner) -> None:
    build = platforms_build() + SECOND_HOST
    _setup(runner, {"p/BUILD": build, "a/BUILD": 'toy_artifact(name="a", text="A")'}, "--platforms-host=musl")
    assert _package(runner, "a", "a") == {"a/a.txt": "A for musl\n"}


def test_host_option_must_match_the_machine(runner: RuleRunner) -> None:
    _setup(
        runner,
        {"p/BUILD": platforms_build(), "a/BUILD": 'toy_artifact(name="a", text="A")'},
        f"--platforms-host={OTHER}",
    )
    with pytest.raises(
        ExecutionError, match=rf"\[platforms\]\.host is `{OTHER}` \(windows, x86_64\), but this machine is"
    ):
        _package(runner, "a", "a")


def test_no_platform_matches_the_host(runner: RuleRunner) -> None:
    build = f'platform(name="{OTHER}", os="windows", arch="x86_64")\n'
    _setup(runner, {"p/BUILD": build, "a/BUILD": 'toy_artifact(name="a", text="A")'})
    assert _package(runner, "a", "a") == {"a/a.txt": f"A for {HOST}\n"}


def test_no_platforms_defined_means_an_implicit_host(runner: RuleRunner) -> None:
    _setup(runner, {"a/BUILD": 'toy_artifact(name="a", text="A")'})
    assert _package(runner, "a", "a") == {"a/a.txt": f"A for {HOST}\n"}


def test_targets_of_alias() -> None:
    runner = rule_runner(QueryRule(TargetsOfAlias, [TargetsOfAliasRequest]))
    runner.write_files(
        {
            "p/BUILD": platforms_build(),
            "q/r/BUILD": 'platform(name="r", os="linux", arch="x86_64")\ntoy_artifact(name="a", text="A")\n',
        }
    )
    found = runner.request(TargetsOfAlias, [TargetsOfAliasRequest("platform")])
    assert sorted(str(t.address) for t in found) == sorted([f"p:{HOST}", f"p:{OTHER}", "q/r:r"])
    assert [
        str(t.address) for t in runner.request(TargetsOfAlias, [TargetsOfAliasRequest("toy_artifact")])
    ] == ["q/r:a"]
