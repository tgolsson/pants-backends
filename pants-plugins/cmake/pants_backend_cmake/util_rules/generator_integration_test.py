"""`cmake_project` generating `cmake_target` handles, with the real CMake and the host compiler."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import pytest
from pants.backend.experimental.cc import register as pants_cc_register
from pants.backend.project_info import dependents, list_targets
from pants.backend.project_info.dependents import Dependents, DependentsRequest
from pants.backend.project_info.list_targets import List
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.target_types import FilesGeneratorTarget
from pants.core.util_rules import external_tool, source_files, system_binaries
from pants.engine.addresses import Address, Addresses
from pants.engine.internals.parametrize import Parametrize
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    DependenciesRequest,
    StringSequenceField,
    Tags,
    Target,
)
from pants.engine.unions import UnionRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register as cc_register
from pants_backend_cc.util_rules.outputs import DeclaredOutputs, DeclaredOutputsFieldSet
from pants_backend_cmake import register
from pants_backend_cmake.ninja.parser import NinjaParseError
from pants_backend_cmake.target_types import (
    CMakeBuildTypeField,
    CMakeTargetNameField,
    CMakeTargetProjectField,
)
from pants_backend_cmake.util_rules import generator
from pants_backend_cmake.util_rules.canonicalize import CanonicalizeError
from pants_backend_cmake.util_rules.configure import ConfiguredProject, ConfigureRequest
from pants_backend_cmake.util_rules.targets import CMakeTargetRequest, ResolvedCMakeTarget
from pants_backend_platforms import register as platforms_register
from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS, OTHER, platforms_build
from pants_backend_platforms.util_rules.platforms import TargetPlatform

HOST_PLATFORM = TargetPlatform(HOST, HOST_OS, HOST_ARCH)
WINDOWS = OTHER

CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(demo LANGUAGES C)
add_library(demo STATIC lib.c)
add_executable(app main.c)
target_link_libraries(app demo)
"""

FILES = {
    "BUILD": 'cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"])\n',
    "CMakeLists.txt": CMAKELISTS,
    "lib.c": "int demo(void) { return 42; }\n",
    "main.c": "int demo(void);\nint main(void) { return demo() == 42 ? 0 : 1; }\n",
}


class FakeOutputsField(StringSequenceField):
    alias = "outputs"
    help = "Files a generator would write."


class FakeGeneratorTarget(Target):
    alias = "fake_generator"
    core_fields = (*COMMON_TARGET_FIELDS, FakeOutputsField)
    help = "Declares outputs it never writes."


@dataclass(frozen=True)
class FakeOutputsFieldSet(DeclaredOutputsFieldSet):
    required_fields = (FakeOutputsField,)

    outputs: FakeOutputsField


@rule
async def fake_outputs(field_set: FakeOutputsFieldSet) -> DeclaredOutputs:
    return DeclaredOutputs(tuple(field_set.outputs.value or ()))


class FakeConsumerTarget(Target):
    alias = "fake_consumer"
    core_fields = (*COMMON_TARGET_FIELDS, CMakeTargetProjectField, CMakeTargetNameField)
    help = "Names a CMake target, with no `build_type` field."


def _rule_runner(*extra: object, target_types: tuple[type[Target], ...] = ()) -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *source_files.rules(),
            *system_binaries.rules(),
            *core_target_types.rules(),
            *list_targets.rules(),
            *dependents.rules(),
            *register.rules(),
            fake_outputs,
            UnionRule(DeclaredOutputsFieldSet, FakeOutputsFieldSet),
            QueryRule(ResolvedCMakeTarget, [CMakeTargetRequest]),
            QueryRule(ConfiguredProject, [ConfigureRequest]),
            QueryRule(Dependents, [DependentsRequest]),
            QueryRule(Addresses, [DependenciesRequest]),
            *extra,
        ],
        target_types=[
            *register.target_types(),
            *platforms_register.target_types(),
            *cc_register.target_types(),
            FilesGeneratorTarget,
            FakeGeneratorTarget,
            FakeConsumerTarget,
            *target_types,
        ],
        objects={"parametrize": Parametrize},
    )


@pytest.fixture
def rule_runner() -> RuleRunner:
    return _rule_runner()


def _write(rule_runner: RuleRunner, **overrides: str) -> None:
    rule_runner.write_files({**FILES, **overrides})
    rule_runner.set_options([], env_inherit={"PATH"})


def _listed(rule_runner: RuleRunner, *specs: str) -> list[str]:
    result = rule_runner.run_goal_rule(List, args=list(specs or ("::",)), env_inherit={"PATH"})
    assert result.exit_code == 0, result.stderr
    return result.stdout.splitlines()


def _handle(name: str, spec_path: str = "", **parameters: str) -> Address:
    return Address(spec_path, target_name="proj", generated_name=name, parameters=parameters)


def test_handles(rule_runner: RuleRunner) -> None:
    _write(rule_runner, BUILD=FILES["BUILD"].replace(")", ', tags=["c"])'))
    assert _listed(rule_runner) == [
        "//:proj",
        "//:proj#_inputs",
        "//:proj#app",
        "//:proj#demo",
        "//CMakeLists.txt:proj",
        "//lib.c:proj",
        "//main.c:proj",
    ]
    handle = rule_runner.get_target(_handle("demo"))
    assert handle.alias == "cmake_target"
    assert handle[CMakeTargetProjectField].value == "//:proj"
    assert handle[CMakeTargetNameField].value == "demo"
    assert handle[Tags].value == ("c",)
    deps = rule_runner.request(Addresses, [DependenciesRequest(handle[Dependencies])])
    assert list(deps) == [_handle("_inputs")]
    resolved = rule_runner.request(ResolvedCMakeTarget, [CMakeTargetRequest(_handle("demo"), HOST_PLATFORM)])
    assert (resolved.name, resolved.info.kind) == ("demo", "STATIC_LIBRARY")


def test_overrides_and_moved_fields(rule_runner: RuleRunner) -> None:
    platforms = platforms_build()
    _write(
        rule_runner,
        BUILD='cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], build_type="Release", '
        f'overrides={{"app": {{"platforms": ["{HOST}", "{WINDOWS}"]}}}})\n',
        **{"platforms/BUILD": platforms},
    )
    app, demo = (rule_runner.get_target(_handle(n)) for n in ("app", "demo"))
    assert app[PlatformsField].value == (HOST, WINDOWS)
    assert demo[PlatformsField].value is None
    assert (app[CMakeBuildTypeField].value, demo[CMakeBuildTypeField].value) == ("Release", "Release")


def test_unknown_override(rule_runner: RuleRunner) -> None:
    _write(
        rule_runner,
        BUILD='cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], '
        'overrides={"nope": {"tags": ["x"]}})\n',
    )
    with pytest.raises(
        ExecutionError, match=r"`overrides` names targets the project does not define: \['nope'\]"
    ):
        rule_runner.get_target(_handle("demo"))


def test_parametrized_build_type(rule_runner: RuleRunner) -> None:
    _write(
        rule_runner,
        BUILD='cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], '
        'build_type=parametrize("Debug", "Release"))\n',
    )
    assert {"//:proj#demo@build_type=Debug", "//:proj#demo@build_type=Release"} <= set(_listed(rule_runner))
    commands = {}
    for build_type in ("Debug", "Release"):
        resolved = rule_runner.request(
            ResolvedCMakeTarget, [CMakeTargetRequest(_handle("demo", build_type=build_type), HOST_PLATFORM)]
        )
        edge = resolved.project.graph.producers["CMakeFiles/demo.dir/lib.c.o"]
        commands[build_type] = resolved.project.graph.command(edge)
    assert " -g" in commands["Debug"] and "-O3" not in commands["Debug"]
    assert "-O3" in commands["Release"]
    # Each handle depends only on its own parametrization's inputs, not on the project (a cycle).
    handle = rule_runner.get_target(_handle("demo", build_type="Debug"))
    deps = rule_runner.request(Addresses, [DependenciesRequest(handle[Dependencies])])
    assert list(deps) == [_handle("_inputs", build_type="Debug")]
    inputs = rule_runner.get_target(_handle("_inputs", build_type="Debug"))
    assert inputs[CMakeBuildTypeField].value == "Debug"
    deps = rule_runner.request(Addresses, [DependenciesRequest(inputs[Dependencies])])
    assert Address("", target_name="proj", parameters={"build_type": "Debug"}) not in deps


def test_failed_configure_generates_no_handles(rule_runner: RuleRunner, caplog) -> None:
    _write(
        rule_runner,
        BUILD=FILES["BUILD"] + 'cmake_target(name="hand", project=":proj", cmake_name="demo")\n',
        **{
            "CMakeLists.txt": CMAKELISTS + "this_is_not_a_command()\n",
            "other/BUILD": 'files(name="data", sources=["*.txt"])\n',
            "other/a.txt": "a\n",
        },
    )
    with caplog.at_level(logging.ERROR):
        listed = _listed(rule_runner)
    assert "//:proj#demo" not in listed and "//:proj#_inputs" in listed and "other/a.txt:data" in listed
    assert "configure for" in caplog.text and "this_is_not_a_command" in caplog.text
    # What `pants package` of a hand-written target runs.
    with pytest.raises(ExecutionError, match="this_is_not_a_command"):
        rule_runner.request(
            ResolvedCMakeTarget, [CMakeTargetRequest(Address("", target_name="hand"), HOST_PLATFORM)]
        )


def test_project_sources_reach_the_handles(rule_runner: RuleRunner) -> None:
    _write(rule_runner)
    found = rule_runner.request(
        Dependents,
        [
            DependentsRequest(
                [Address("", target_name="proj", relative_file_path="lib.c")],
                transitive=True,
                include_roots=False,
            )
        ],
    )
    assert {_handle("demo"), _handle("app"), _handle("_inputs")} <= set(found)


def test_project_in_a_subdirectory(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({f"sub/{path}": text for path, text in FILES.items()})
    rule_runner.set_options([], env_inherit={"PATH"})
    assert "sub:proj#demo" in _listed(rule_runner)
    resolved = rule_runner.request(
        ResolvedCMakeTarget, [CMakeTargetRequest(_handle("demo", "sub"), HOST_PLATFORM)]
    )
    assert resolved.project.build_dir == "sub/build"


def test_declared_output_is_a_placeholder(rule_runner: RuleRunner) -> None:
    # gen/gen.c exists only as an output a generator declares: the configure sees it anyway.
    _write(
        rule_runner,
        BUILD='fake_generator(name="gen", outputs=["gen/gen.c"])\n'
        'cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], dependencies=[":gen"])\n',
        **{"CMakeLists.txt": CMAKELISTS + "add_library(generated STATIC gen/gen.c)\n"},
    )
    assert "//:proj#generated" in _listed(rule_runner)


def test_target_without_build_type(rule_runner: RuleRunner) -> None:
    _write(
        rule_runner,
        BUILD=FILES["BUILD"] + 'fake_consumer(name="consumer", project=":proj", cmake_name="demo")\n',
    )
    resolved = rule_runner.request(
        ResolvedCMakeTarget, [CMakeTargetRequest(Address("", target_name="consumer"), HOST_PLATFORM)]
    )
    assert resolved.name == "demo"


def test_with_cc_and_python_inference() -> None:
    # Inference reads every target; generation must not wait on inference.
    rule_runner = _rule_runner(
        *pants_cc_register.rules(),
        *python_register.rules(),
        target_types=(*pants_cc_register.target_types(), *python_register.target_types()),
    )
    _write(
        rule_runner,
        BUILD='cc_sources(name="c", sources=["*.c"])\n'
        'cmake_project(name="proj", sources=["CMakeLists.txt"], dependencies=[":c"])\n'
        'python_sources(name="py", sources=["*.py"])\n',
        **{"tool.py": "import os\n"},
    )
    assert {"//:proj#demo", "//:proj#app"} <= set(_listed(rule_runner))
    handle = rule_runner.get_target(_handle("app"))
    deps = rule_runner.request(Addresses, [DependenciesRequest(handle[Dependencies])])
    assert list(deps) == [_handle("_inputs")]


def test_target_named_like_the_inputs(rule_runner: RuleRunner) -> None:
    _write(rule_runner, **{"CMakeLists.txt": CMAKELISTS + "add_library(_inputs STATIC lib.c)\n"})
    with pytest.raises(ExecutionError, match="a CMake target named `_inputs` collides"):
        rule_runner.get_target(_handle("demo"))


def _platforms(*extra: str) -> str:
    return f'platform(name="{HOST}", os="{HOST_OS}", arch="{HOST_ARCH}")\n' + "".join(extra)


def test_missing_compiler_skips_the_platform(rule_runner: RuleRunner, caplog) -> None:
    toolchain = (
        f'cc_toolchain(name="native", host="{HOST}", target="{HOST}", cc="no-such-cc", cxx="g++", ar="ar")\n'
    )
    _write(rule_runner, **{"platforms/BUILD": _platforms(toolchain)})
    with caplog.at_level(logging.WARNING):
        listed = _listed(rule_runner)
    assert "//:proj#demo" not in listed and "//:proj#_inputs" in listed
    assert f"not configured for {HOST}" in caplog.text and "Cannot find `no-such-cc`" in caplog.text


@pytest.mark.parametrize(
    "toolchains, error",
    [
        pytest.param(
            f'cc_toolchain(name="a", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", ar="ar")\n'
            f'cc_toolchain(name="b", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", ar="ar")\n',
            "found ['platforms:a', 'platforms:b']",
            id="two-match",
        ),
        pytest.param(
            f'cc_toolchain(name="typo", host="{HOST}", target="windos", cc="gcc", cxx="g++", ar="ar")\n',
            "unknown platform `windos`",
            id="unknown-platform",
        ),
    ],
)
def test_misconfigured_toolchain_warns(rule_runner: RuleRunner, caplog, toolchains: str, error: str) -> None:
    _write(rule_runner, **{"platforms/BUILD": _platforms(toolchains)})
    with caplog.at_level(logging.WARNING):
        listed = _listed(rule_runner)
    assert "//:proj#demo" not in listed
    assert any(r.levelno == logging.WARNING and error in r.getMessage() for r in caplog.records), caplog.text


def test_a_platform_without_a_toolchain_is_skipped_quietly(rule_runner: RuleRunner, caplog) -> None:
    # `nope` may be a target only Windows defines, so it warns rather than fails.
    _write(
        rule_runner,
        BUILD='cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], '
        f'overrides={{"app": {{"platforms": ["{HOST}", "{WINDOWS}"]}}, "nope": {{"tags": ["x"]}}}})\n',
        **{"platforms/BUILD": platforms_build()},
    )
    with caplog.at_level(logging.DEBUG):
        listed = _listed(rule_runner)
    assert {"//:proj#app", "//:proj#demo"} <= set(listed)
    skipped = [r for r in caplog.records if f"not configured for {WINDOWS}" in r.getMessage()]
    assert [r.levelno for r in skipped] == [logging.DEBUG]
    assert any(
        r.levelno == logging.WARNING and "does not define: ['nope']" in r.getMessage() for r in caplog.records
    )


ALT = "alt"
# Built with the host's compilers, but another platform as far as Pants is concerned.
ALT_TOOLCHAIN = (
    f'platform(name="{ALT}", os="{HOST_OS}", arch="{"aarch64" if HOST_ARCH == "x86_64" else "x86_64"}")\n'
    f'cc_toolchain(name="to-alt", host="{HOST}", target="{ALT}", cc="gcc", cxx="g++", ar="ar", '
    'cmake_toolchain_file="alt.cmake")\n'
)


@pytest.mark.parametrize("targets_alt", [False, True])
def test_only_targeted_platforms_are_configured(rule_runner: RuleRunner, targets_alt: bool) -> None:
    overrides = f', overrides={{"demo": {{"platforms": ["{ALT}"]}}}}' if targets_alt else ""
    _write(
        rule_runner,
        BUILD=f'cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"]{overrides})\n',
        **{
            "CMakeLists.txt": CMAKELISTS + "if(ALT)\n  add_library(alt_only STATIC lib.c)\nendif()\n",
            "platforms/BUILD": _platforms(ALT_TOOLCHAIN),
            "platforms/alt.cmake": "set(ALT ON)\n",
        },
    )
    assert ("//:proj#alt_only" in _listed(rule_runner)) == targets_alt


def test_every_source_a_placeholder_is_logged(rule_runner: RuleRunner, caplog) -> None:
    _write(rule_runner, BUILD='cmake_project(name="proj", sources=["*.c"])\n')
    with caplog.at_level(logging.ERROR):
        listed = _listed(rule_runner)
    assert "//:proj#_inputs" in listed and "every source matches `configure_placeholders`" in caplog.text


@pytest.mark.skipif(not os.path.exists("/usr/bin/cc"), reason="needs a second C compiler path")
def test_toolchain_file_setting_the_compiler_is_logged(rule_runner: RuleRunner, caplog) -> None:
    toolchain = (
        f'cc_toolchain(name="native", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", ar="ar", '
        'cmake_toolchain_file="native.cmake")\n'
    )
    _write(
        rule_runner,
        **{
            "platforms/BUILD": _platforms(toolchain),
            "platforms/native.cmake": "set(CMAKE_C_COMPILER /usr/bin/cc)\n",
        },
    )
    with caplog.at_level(logging.ERROR):
        listed = _listed(rule_runner)
    assert "//:proj#demo" not in listed
    assert "configure did not use the `cc_toolchain`'s tools" in caplog.text


@pytest.mark.parametrize("dependencies", [[":f", "!:f"], ["lib.c:f", "!lib.c:f"]])
def test_excluded_dependency(rule_runner: RuleRunner, dependencies: list[str]) -> None:
    # Without the exclusion, `lib.c` would be in both `sources` and a dependency.
    _write(
        rule_runner,
        BUILD='files(name="f", sources=["lib.c"])\n'
        f'cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], dependencies={dependencies!r})\n',
    )
    assert "//:proj#demo" in _listed(rule_runner)
    rule_runner.request(ConfiguredProject, [ConfigureRequest(Address("", target_name="proj"), HOST_PLATFORM)])


def test_reserved_overrides(rule_runner: RuleRunner) -> None:
    _write(
        rule_runner,
        BUILD='cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"], '
        'overrides={"app": {"dependencies": [":x"], "cmake_name": "demo"}})\n',
    )
    with pytest.raises(ExecutionError, match=r"`overrides` cannot set \['cmake_name', 'dependencies'\]"):
        rule_runner.get_target(_handle("app"))


def test_a_second_session(rule_runner: RuleRunner) -> None:
    _write(rule_runner)
    assert "//:proj#demo" in _listed(rule_runner)
    rule_runner.new_session("second")
    rule_runner.set_options([], env_inherit={"PATH"})
    assert "//:proj#demo" in _listed(rule_runner)


def test_files_dependency(rule_runner: RuleRunner) -> None:
    _write(
        rule_runner,
        BUILD=FILES["BUILD"].replace(")", ', dependencies=["other:src"])'),
        **{
            "CMakeLists.txt": CMAKELISTS + "add_library(other STATIC other/x.c)\n",
            "other/BUILD": 'files(name="src", sources=["*.c"])\n',
            "other/x.c": "int x(void) { return 1; }\n",
        },
    )
    assert "//:proj#other" in _listed(rule_runner)
    resolved = rule_runner.request(ResolvedCMakeTarget, [CMakeTargetRequest(_handle("other"), HOST_PLATFORM)])
    assert resolved.info.kind == "STATIC_LIBRARY"


def _release_flags(rule_runner: RuleRunner, address: Address) -> bool:
    resolved = rule_runner.request(ResolvedCMakeTarget, [CMakeTargetRequest(address, HOST_PLATFORM)])
    edge = resolved.project.graph.producers["CMakeFiles/demo.dir/lib.c.o"]
    return "-O3" in resolved.project.graph.command(edge)


@pytest.mark.parametrize(
    "project_build_type, project, own_build_type, release",
    [
        pytest.param(
            'parametrize("Debug", "Release")', ":proj@build_type=Release", None, True, id="parameter"
        ),
        pytest.param(
            'parametrize(dbg="Debug", rel="Release")', ":proj@build_type=rel", None, True, id="named"
        ),
        pytest.param('"Release"', ":proj", None, True, id="project"),
        pytest.param('"Release"', ":proj", "Debug", False, id="own-wins"),
        pytest.param(
            'parametrize("Debug", "Release")', ":proj@build_type=Debug", "Release", True, id="own-wins-p"
        ),
    ],
)
def test_hand_written_target_build_type(
    rule_runner: RuleRunner, project_build_type: str, project: str, own_build_type: str | None, release: bool
) -> None:
    own = f', build_type="{own_build_type}"' if own_build_type else ""
    _write(
        rule_runner,
        BUILD=_project(build_type=project_build_type)
        + f'cmake_target(name="hand", project="{project}", cmake_name="demo"{own})\n',
    )
    assert _release_flags(rule_runner, Address("", target_name="hand")) == release


def _project(**fields: str) -> str:
    extra = "".join(f", {k}={v}" for k, v in fields.items())
    return f'cmake_project(name="proj", sources=["CMakeLists.txt", "*.c"]{extra})\n'


def _assert_degraded(rule_runner: RuleRunner, caplog, message: str) -> None:
    with caplog.at_level(logging.ERROR):
        listed = _listed(rule_runner)
    assert "//:proj#demo" not in listed and "//:proj#_inputs" in listed
    assert any(
        r.levelno == logging.ERROR and "generates no targets" in r.getMessage() and message in r.getMessage()
        for r in caplog.records
    ), caplog.text


@pytest.mark.parametrize(
    "build, message, build_message",
    [
        pytest.param(
            'files(name="f", sources=["lib.c"])\n' + _project(dependencies='[":f"]'),
            "`sources` and a dependency both provide ['lib.c']",
            "`sources` and a dependency both provide ['lib.c']",
            id="overlap",
        ),
        pytest.param(_project(dependencies='[":missing"]'), "//:missing", "//:missing", id="missing"),
        pytest.param(_project(dependencies='["a:b:c"]'), "a:b:c", "a:b:c", id="malformed"),
        pytest.param(
            'fake_generator(name="gen", tags=parametrize(a=["x"], b=["y"]), outputs=["gen/a.c"])\n'
            + _project(dependencies='[":gen"]'),
            "was not generated by the target `//:gen`",
            "does not provide enough address parameters",
            id="parametrized",
        ),
    ],
)
def test_dependency_mistakes_are_logged(
    rule_runner: RuleRunner, caplog, build: str, message: str, build_message: str
) -> None:
    _write(rule_runner, BUILD=build)
    _assert_degraded(rule_runner, caplog, message)
    # The build still raises them.
    with pytest.raises(ExecutionError, match=re.escape(build_message)):
        rule_runner.request(
            ConfiguredProject, [ConfigureRequest(Address("", target_name="proj"), HOST_PLATFORM)]
        )


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"platforms": '["nope"]'}, id="project"),
        pytest.param({"overrides": '{"app": {"platforms": ["nope"]}}'}, id="override"),
    ],
)
def test_unknown_platform_is_logged(rule_runner: RuleRunner, caplog, fields: dict[str, str]) -> None:
    _write(rule_runner, BUILD=_project(**fields))
    _assert_degraded(rule_runner, caplog, "unknown platform `nope`")


@pytest.mark.parametrize("build_dir", [".", "", "/abs", "../out", "out/../.."])
def test_bad_build_dir_is_logged(rule_runner: RuleRunner, caplog, build_dir: str) -> None:
    _write(rule_runner, BUILD=_project(build_dir=repr(build_dir)))
    message = f"`build_dir` must be a subdirectory of the project's directory, not `{build_dir}`"
    _assert_degraded(rule_runner, caplog, message)
    with pytest.raises(ExecutionError, match=re.escape(message)):
        rule_runner.request(
            ConfiguredProject, [ConfigureRequest(Address("", target_name="proj"), HOST_PLATFORM)]
        )


@pytest.mark.parametrize("error", [CanonicalizeError, NinjaParseError])
def test_errors_reading_the_configure_are_logged(
    rule_runner: RuleRunner, caplog, monkeypatch, error: type[Exception]
) -> None:
    async def broken(*args: object) -> None:
        raise error("unreadable")

    monkeypatch.setattr(generator, "canonical_tree", broken)
    _write(rule_runner)
    with caplog.at_level(logging.ERROR):
        listed = _listed(rule_runner)
    assert "//:proj#demo" not in listed and "//:proj#_inputs" in listed
    assert f"configure for {HOST} failed" in caplog.text and "unreadable" in caplog.text


@pytest.mark.parametrize(
    "options, reason",
    [
        pytest.param(
            [
                "--cmake-version=9.9.9",
                f"--cmake-known-versions=['9.9.9|linux_x86_64|{'0' * 64}|10', "
                f"'9.9.9|linux_arm64|{'0' * 64}|10']",
                "--cmake-url-template=file:///nonexistent/cmake-{version}.tar.gz",
            ],
            "/nonexistent/cmake-9.9.9.tar.gz",
            id="failed-download",
        ),
        pytest.param(["--cmake-version=9.9.8"], "9.9.8", id="unknown-version"),
    ],
)
def test_cmake_download_failure_skips_the_platform(
    rule_runner: RuleRunner, caplog, options: list[str], reason: str
) -> None:
    _write(rule_runner)
    with caplog.at_level(logging.WARNING):
        result = rule_runner.run_goal_rule(List, global_args=options, args=["::"], env_inherit={"PATH"})
    assert result.exit_code == 0, result.stderr
    listed = result.stdout.splitlines()
    assert "//:proj#demo" not in listed and "//:proj#_inputs" in listed
    assert any(
        r.levelno == logging.WARNING
        and f"not configured for {HOST}" in r.getMessage()
        and reason in r.getMessage()
        for r in caplog.records
    ), caplog.text


def test_unknown_cmake_name_names_the_platform(rule_runner: RuleRunner) -> None:
    _write(
        rule_runner, BUILD=FILES["BUILD"] + 'cmake_target(name="hand", project=":proj", cmake_name="nope")\n'
    )
    with pytest.raises(
        ExecutionError, match=f"CMake target `nope` is not a library or executable of //:proj for {HOST}"
    ):
        rule_runner.request(
            ResolvedCMakeTarget, [CMakeTargetRequest(Address("", target_name="hand"), HOST_PLATFORM)]
        )
