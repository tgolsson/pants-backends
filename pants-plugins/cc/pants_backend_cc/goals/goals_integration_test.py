"""`cc_test` with the host compiler against `toy_library`, a provider without a build system: the
test build depends on the link interface alone."""

from __future__ import annotations

from typing import Any

import pytest
from pants.core import target_types as core_target_types
from pants.core.goals import test as core_test
from pants.core.goals.test import TestResult
from pants.core.target_types import FilesGeneratorTarget, RelocatedFiles
from pants.core.util_rules import env_vars, source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import CreateDigest, DigestContents, FileContent
from pants.engine.internals.parametrize import Parametrize
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.intrinsics import create_digest
from pants.engine.rules import QueryRule, rule
from pants.engine.target import Tags, Target
from pants.engine.unions import UnionRule
from pants.source import source_root
from pants.testutil.rule_runner import RuleRunner
from pants.util.frozendict import FrozenDict

from pants_backend_cc import register
from pants_backend_cc.goals.test import CcTestFieldSet, CcTestRequest
from pants_backend_cc.goals.wrapper import CcTestWrapper, CcTestWrapperError, CcTestWrapperRequest
from pants_backend_cc.target_types import CcTestSourcesField
from pants_backend_cc.testutil import GenTarget, codegen_rules
from pants_backend_cc.toy_library import LIBRARY_SHADOW_RULES, ToyLibraryTarget, toy_library_rules
from pants_backend_cc.util_rules import executable

BUILD = """\
toy_library(name="demo", sources=["demo.c", "demo.h"])
files(name="framework", sources=["framework/*"])
files(name="data", sources=["data/*.txt"])

# One test per directory under tests/. The framework's check.c is compiled into every test.
cc_tests(
    name="tests",
    sources=["tests/**/*.c", "tests/**/*.h"],
    dependencies=[":demo", ":framework"],
    include_dirs=["framework"],
    args=["--verbose", "extra"],
    overrides={
        "tests/failing": {"args": []},
        "tests/data": {"dependencies": [":demo", ":data"], "extra_env_vars": ["DEMO_GREETING=hello"]},
    },
)

cc_test(
    name="single",
    sources=["single.c", "extra/single.h"],
    dependencies=[":demo"],
    include_dirs=["extra"],
    defines=["SINGLE_SCALE=2"],
    compiler_args=["-Wall", "-Werror"],
    linker_args=["-lm"],
)
"""

FILES = {
    "BUILD": BUILD,
    "demo.c": "int demo(void) { return 42; }\n",
    "demo.h": "int demo(void);\n",
    "framework/check.h": "int check(int actual, int expected);\n",
    "framework/check.c": '#include <stdio.h>\n#include "check.h"\n'
    "int check(int actual, int expected) { if (actual == expected) return 0; "
    'printf("expected %d, got %d\\n", expected, actual); return 1; }\n',
    "tests/pass/main.c": '#include <stdio.h>\n#include "demo.h"\n#include "check.h"\n#include "helper.h"\n'
    'int main(int argc, char **argv) { printf("args=%d\\n", argc); '
    "return check(demo(), 42) + check(helper(), 1); }\n",
    "tests/pass/helper.h": "static inline int helper(void) { return 1; }\n",
    "tests/failing/main.c": '#include <stdio.h>\nint main(void) { puts("nope"); return 3; }\n',
    "tests/data/main.c": "#include <stdio.h>\n#include <stdlib.h>\n"
    'int main(void) { FILE *f = fopen("data/input.txt", "r"); if (!f) return 2; '
    'char buf[32] = {0}; fgets(buf, sizeof buf, f); printf("%s %s\\n", getenv("DEMO_GREETING"), buf); return 0; }\n',  # noqa: E501
    "tests/broken/main.c": "int main(void) { return not_declared_anywhere(); }\n",
    "data/input.txt": "world\n",
    "extra/single.h": "#define SINGLE_BASE 21\n",
    "single.c": '#include <math.h>\n#include "demo.h"\n#include "single.h"\n'
    "int main(void) { return (int)sqrt((double)(SINGLE_BASE * SINGLE_SCALE)) == 6 && demo() == 42 ? 0 : 1; }\n",  # noqa: E501
}


class WrapTagged(CcTestWrapperRequest):
    @classmethod
    def is_applicable(cls, target: Target) -> bool:
        return "wrapped" in (target[Tags].value or ())


@rule
async def wrap_tagged(request: WrapTagged) -> CcTestWrapper:
    script = await create_digest(
        CreateDigest(
            [FileContent("run.sh", b'#!/bin/sh\necho "wrapped by $WRAPPER"\nexec "$@"\n', is_executable=True)]
        )
    )
    return CcTestWrapper(
        argv_prefix=("{chroot}/wrapper/run.sh",),
        immutable_input_digests=FrozenDict({"wrapper": script}),
        env=FrozenDict({"WRAPPER": "demo"}),
    )


class WrapTaggedToo(WrapTagged):
    """A second wrapper for the same tests, which is an error."""


@rule
async def wrap_tagged_too(request: WrapTaggedToo) -> CcTestWrapper:
    return CcTestWrapper()


class WrapBroken(CcTestWrapperRequest):
    @classmethod
    def is_applicable(cls, target: Target) -> bool:
        return "broken-wrapper" in (target[Tags].value or ())


@rule
async def wrap_broken(request: WrapBroken) -> CcTestWrapper:
    raise CcTestWrapperError("no emulator today")


class WrapBuggy(CcTestWrapperRequest):
    @classmethod
    def is_applicable(cls, target: Target) -> bool:
        return "buggy-wrapper" in (target[Tags].value or ())


@rule
async def wrap_buggy(request: WrapBuggy) -> CcTestWrapper:
    raise ValueError("a bug in the wrapper")


def _rule_runner(*extra_rules: object) -> RuleRunner:
    return RuleRunner(
        rules=[
            wrap_tagged,
            UnionRule(CcTestWrapperRequest, WrapTagged),
            *extra_rules,
            *source_files.rules(),
            *system_binaries.rules(),
            *core_target_types.rules(),
            *core_test.rules(),
            *env_vars.rules(),
            *source_root.rules(),
            *codegen_rules(),
            *toy_library_rules(),
            *register.rules(),
            QueryRule(TestResult, [CcTestRequest.Batch]),
            QueryRule(executable.BuiltTest, [executable.TestBuildRequest]),
        ],
        target_types=[
            *register.target_types(),
            FilesGeneratorTarget,
            RelocatedFiles,
            GenTarget,
            ToyLibraryTarget,
        ],
        objects={"parametrize": Parametrize},
    )


@pytest.fixture
def rule_runner() -> RuleRunner:
    return _rule_runner()


def _write_project(rule_runner: RuleRunner, **overrides: str) -> None:
    rule_runner.write_files({**FILES, **overrides})
    rule_runner.set_options([], env_inherit={"PATH"})


def _test(rule_runner: RuleRunner, address: Address) -> TestResult:
    target = rule_runner.get_target(address)
    batch: Any = CcTestRequest.Batch("", (CcTestFieldSet.create(target),), None)
    return rule_runner.request(TestResult, [batch])


def _generated(name: str) -> Address:
    return Address("", target_name="tests", generated_name=name)


def test_generated_tests(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    generated = rule_runner.get_target(_generated("tests/pass"))
    # The directory's translation units plus every header under the generator's sources.
    assert generated[CcTestSourcesField].value == ("tests/pass/main.c", "tests/pass/helper.h")
    assert rule_runner.get_target(_generated("tests/failing"))[CcTestSourcesField].value == (
        "tests/failing/main.c",
        "tests/pass/helper.h",
    )


def test_unknown_override_key(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner, BUILD=BUILD.replace('"tests/failing": {"args": []}', '"nope": {"args": []}'))
    with pytest.raises(ExecutionError, match="names tests that are not generated: \\['nope'\\]"):
        rule_runner.get_target(_generated("tests/pass"))


def test_run_tests(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    # The library's compile flags and archive, a framework file compiled in, a header next to
    # the test, `args`.
    passing = _test(rule_runner, _generated("tests/pass"))
    assert passing.exit_code == 0, passing.stdout_bytes.decode() + passing.stderr_bytes.decode()
    assert "args=3" in passing.stdout_bytes.decode()

    failing = _test(rule_runner, _generated("tests/failing"))
    assert failing.exit_code == 3
    assert "nope" in failing.stdout_bytes.decode()

    # Overridden `dependencies` stage the data files; `extra_env_vars` are passed.
    with_data = _test(rule_runner, _generated("tests/data"))
    assert with_data.exit_code == 0, with_data.stderr_bytes.decode()
    assert "hello world" in with_data.stdout_bytes.decode()


def test_build_failure_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    broken = _test(rule_runner, _generated("tests/broken"))
    assert broken.exit_code == 1
    assert "not_declared_anywhere" in broken.stderr_bytes.decode()


def test_missing_header_names_the_test_and_the_include(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner, BUILD=BUILD.replace('sources=["single.c", "extra/single.h"]', 'sources=["single.c"]')
    )
    result = _test(rule_runner, Address("", target_name="single"))
    assert result.exit_code == 1
    message = result.stderr_bytes.decode()
    assert message.startswith(
        "//:single: single.c includes files that none of its inputs provide: ['single.h']"
    )


def test_single_test_with_own_flags(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    # `include_dirs`, `defines`, `compiler_args` and `linker_args`.
    single = _test(rule_runner, Address("", target_name="single"))
    assert single.exit_code == 0, single.stderr_bytes.decode()


def test_wrapper(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner, BUILD=BUILD.replace('name="single",', 'name="single",\n    tags=["wrapped"],')
    )
    # The wrapper's script is mounted at `{chroot}/wrapper`, runs before the executable with
    # the wrapper's env, and the test's own result comes through.
    single = _test(rule_runner, Address("", target_name="single"))
    assert single.exit_code == 0, single.stderr_bytes.decode()
    assert "wrapped by demo" in single.stdout_bytes.decode()

    # A test no wrapper applies to runs as is.
    passing = _test(rule_runner, _generated("tests/pass"))
    assert passing.exit_code == 0, passing.stderr_bytes.decode()
    assert "wrapped" not in passing.stdout_bytes.decode()


def test_wrapper_problems_are_failed_tests() -> None:
    rule_runner = _rule_runner(
        wrap_tagged_too,
        UnionRule(CcTestWrapperRequest, WrapTaggedToo),
        wrap_broken,
        UnionRule(CcTestWrapperRequest, WrapBroken),
        wrap_buggy,
        UnionRule(CcTestWrapperRequest, WrapBuggy),
    )
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('name="single",', 'name="single",\n    tags=["wrapped"],')
        + 'cc_test(name="broken", sources=["single.c", "extra/single.h"], dependencies=[":demo"],'
        ' include_dirs=["extra"], defines=["SINGLE_SCALE=2"], linker_args=["-lm"], tags=["broken-wrapper"])\n'
        + 'cc_test(name="buggy", sources=["single.c", "extra/single.h"], dependencies=[":demo"],'
        ' include_dirs=["extra"], defines=["SINGLE_SCALE=2"], linker_args=["-lm"], tags=["buggy-wrapper"])\n',
    )
    # Two wrappers apply to `single`; the wrapper of `broken` cannot be set up. Each is that
    # test's failure, not the goal's. A wrapper whose rule has a bug is not a failed test.
    two = _test(rule_runner, Address("", target_name="single"))
    assert two.exit_code == 1
    assert "more than one test wrapper applies: ['WrapTagged', 'WrapTaggedToo']" in two.stderr_bytes.decode()
    broken = _test(rule_runner, Address("", target_name="broken"))
    assert broken.exit_code == 1
    assert "WrapBroken failed: no emulator today" in broken.stderr_bytes.decode()
    with pytest.raises(ExecutionError, match="a bug in the wrapper"):
        _test(rule_runner, Address("", target_name="buggy"))


def test_generated_data_files(rule_runner: RuleRunner) -> None:
    # A dependency that generates files (here `relocated_files`) is staged like a `files` one.
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('"dependencies": [":demo", ":data"]', '"dependencies": [":demo", ":moved"]')
        + 'files(name="raw", sources=["raw/*.txt"])\n'
        + 'relocated_files(name="moved", files_targets=[":raw"], src="raw", dest="data")\n',
        **{"raw/input.txt": "moved\n"},
    )
    with_data = _test(rule_runner, _generated("tests/data"))
    assert with_data.exit_code == 0, with_data.stderr_bytes.decode()
    assert "hello moved" in with_data.stdout_bytes.decode()


def test_parametrizations_share_the_build(rule_runner: RuleRunner) -> None:
    # Parametrizations that differ only in how the test runs build the same executable at the
    # same path, so every compile and link process is shared.
    _write_project(
        rule_runner,
        BUILD=BUILD.replace(
            'name="single",', 'name="single",\n    **parametrize("a"),\n    **parametrize("b", args=["x"]),'
        ),
    )
    a, b = (
        rule_runner.request(
            executable.BuiltTest,
            [executable.TestBuildRequest(Address("", target_name="single", parameters={"parametrize": p}))],
        )
        for p in ("a", "b")
    )
    assert a.executable == b.executable == "__pants_tests/.single/bin/single"
    assert a.runtime == b.runtime


def test_without_translation_units_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner, BUILD=BUILD + 'cc_test(name="empty", dependencies=[":demo"])\n')
    result = _test(rule_runner, Address("", target_name="empty"))
    assert result.exit_code == 1
    assert "no translation unit to compile in `sources` or `dependencies`" in result.stderr_bytes.decode()


def test_without_a_library_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner, BUILD=BUILD + 'cc_test(name="alone", sources=["single.c"])\n')
    result = _test(rule_runner, Address("", target_name="alone"))
    assert result.exit_code == 1
    assert "`dependencies` must name at least one library" in result.stderr_bytes.decode()


def test_libraries_link_in_declared_order_with_repeats(rule_runner: RuleRunner) -> None:
    # Both libraries need `base`: each brings it after itself, so the second still finds it.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="base", sources=["chain/base.c"])\n'
        + 'toy_library(name="first", sources=["chain/first.c"], link_with=["base"])\n'
        + 'toy_library(name="second", sources=["chain/second.c"], link_with=["base"])\n'
        + 'cc_test(name="chain", sources=["chain/main.c"], dependencies=[":first", ":second"])\n',
        **{
            "chain/base.c": "int base(void) { return 20; }\n",
            "chain/first.c": "int base(void);\nint first(void) { return base() + 1; }\n",
            "chain/second.c": "int base(void);\nint second(void) { return base() + 1; }\n",
            "chain/main.c": "int first(void);\nint second(void);\n"
            "int main(void) { return first() + second() == 42 ? 0 : 1; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="chain"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_link_flags_of_every_library(rule_runner: RuleRunner) -> None:
    # Each library calls a function only its own link flag defines.
    _write_project(
        rule_runner,
        BUILD=BUILD + 'toy_library(name="alpha", sources=["flags/alpha.c"], '
        'link_flags=["-Xlinker", "--defsym", "-Xlinker", "alpha_hook=alpha_impl"])\n'
        + 'toy_library(name="beta", sources=["flags/beta.c"], '
        'link_flags=["-Xlinker", "--defsym", "-Xlinker", "beta_hook=beta_impl"])\n'
        + 'cc_test(name="flags", sources=["flags/main.c"], dependencies=[":alpha", ":beta"])\n',
        **{
            "flags/alpha.c": "int alpha_impl(void) { return 2; }\nint alpha_hook(void);\n"
            "int alpha(void) { return alpha_hook(); }\n",
            "flags/beta.c": "int beta_impl(void) { return 40; }\nint beta_hook(void);\n"
            "int beta(void) { return beta_hook(); }\n",
            "flags/main.c": "int alpha(void);\nint beta(void);\n"
            "int main(void) { return alpha() + beta() == 42 ? 0 : 1; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="flags"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_shared_library_and_its_runtime_files(rule_runner: RuleRunner) -> None:
    # The run path finds the shared library; the file it needs at run time is staged too.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="dyn", sources=["dyn/dyn.c"], shared=True)\n'
        + 'cc_test(name="dynamic", sources=["dyn/main.c"], dependencies=[":dyn"])\n',
        **{
            "dyn/dyn.c": "int dyn(void) { return 42; }\n",
            "dyn/main.c": "#include <stdio.h>\nint dyn(void);\n"
            'int main(void) { char buf[8] = {0}; FILE *f = fopen("toybuild/libdyn.data", "r"); '
            "if (!f || !fgets(buf, sizeof buf, f)) return 2; "
            'printf("%s", buf); return dyn() == 42 ? 0 : 1; }\n',
        },
    )
    result = _test(rule_runner, Address("", target_name="dynamic"))
    assert result.exit_code == 0, result.stdout_bytes.decode() + result.stderr_bytes.decode()
    assert result.stdout_bytes.decode() == "dyn\n"


def test_cxx_translation_unit(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD + 'cc_test(name="cxx", sources=["cxx/main.cpp"], dependencies=[":demo"])\n',
        **{
            "cxx/main.cpp": '#include <string>\nextern "C" {\n#include "demo.h"\n}\n'
            'int main() { return std::string("forty-two").size() + 33 == (unsigned)demo() ? 0 : 1; }\n'
        },
    )
    result = _test(rule_runner, Address("", target_name="cxx"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_library_without_the_language_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="c_only", sources=["demo.c"], languages=["c"])\n'
        + 'cc_test(name="cxx", sources=["cxx/main.cpp"], dependencies=[":c_only"])\n',
        **{"cxx/main.cpp": "int main() { return 0; }\n"},
    )
    result = _test(rule_runner, Address("", target_name="cxx"))
    assert result.exit_code == 1
    assert "//:cxx: //:c_only has no cxx consumers: its `languages` are ['c']" in result.stderr_bytes.decode()


def test_a_library_several_ways_is_a_failed_test() -> None:
    rule_runner = _rule_runner(*LIBRARY_SHADOW_RULES)
    _write_project(rule_runner)
    result = _test(rule_runner, Address("", target_name="single"))
    assert result.exit_code == 1
    assert (
        "//:single: //:demo is a library several ways: ['ToyLibraryFieldSet', 'ToyLibraryShadowFieldSet']"
        in result.stderr_bytes.decode()
    )


def test_coverage_notes(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD.replace('linker_args=["-lm"],', 'linker_args=["-lm", "--coverage"],').replace(
            'compiler_args=["-Wall", "-Werror"],', 'compiler_args=["-Wall", "-Werror", "--coverage"],'
        ),
    )
    built = rule_runner.request(
        executable.BuiltTest, [executable.TestBuildRequest(Address("", target_name="single"))]
    )
    notes = rule_runner.request(DigestContents, [built.coverage_notes])
    assert [fc.path for fc in notes] == ["__pants_tests/.single/obj/single.c.gcno"]


def test_compiles_generated_runner(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'test_gen(name="runner", sources=["runner/runner.cpp.in"])\n'
        + 'cc_test(name="with_runner", sources=["runner/cases.c"], dependencies=[":demo", ":runner"])\n',
        **{
            "runner/runner.cpp.in": 'extern "C" int run_cases(void);\nint main() { return run_cases(); }\n',
            "runner/cases.c": '#include "demo.h"\nint run_cases(void) { return demo() == 42 ? 0 : 1; }\n',
        },
    )
    result = _test(rule_runner, Address("", target_name="with_runner"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_sources_overlapping_generated_runner_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'test_gen(name="runner", sources=["runner/runner.cpp.in"])\n'
        + 'cc_test(name="stale", sources=["runner/*.c", "runner/*.cpp"], '
        'dependencies=[":demo", ":runner"])\n',
        **{
            "runner/runner.cpp.in": "int main() { return 0; }\n",
            "runner/runner.cpp": "int main() { return 1; }\n",
            "runner/cases.c": "int run_cases(void) { return 0; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="stale"))
    assert result.exit_code == 1
    message = result.stderr_bytes.decode()
    assert "`sources` and a dependency both provide ['runner/runner.cpp']" in message
    assert '"!runner/runner.cpp" in `sources`' in message


def test_includes_source_roots(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'files(name="vendor", sources=["vendor/fw/*.h"])\n'
        + 'cc_test(name="rooted", sources=["rooted.c"], dependencies=[":demo", ":vendor"])\n',
        **{
            "vendor/fw/unity_like.h": "#define UNITY_LIKE 1\n",
            "rooted.c": '#include "unity_like.h"\nint main(void) { return UNITY_LIKE == 1 ? 0 : 1; }\n',
        },
    )
    rule_runner.set_options(["--source-root-patterns=['/', '/vendor/fw']"], env_inherit={"PATH"})
    result = _test(rule_runner, Address("", target_name="rooted"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


def test_libraries_link_in_declared_not_sorted_order(rule_runner: RuleRunner) -> None:
    # `zuser` needs `aprov`, so only the declared order links.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="zuser", sources=["order/zuser.c"])\n'
        + 'toy_library(name="aprov", sources=["order/aprov.c"])\n'
        + 'cc_test(name="order", sources=["order/main.c"], dependencies=[":zuser", ":aprov"])\n',
        **{
            "order/zuser.c": "int aprov(void);\nint zuser(void) { return aprov() + 2; }\n",
            "order/aprov.c": "int aprov(void) { return 40; }\n",
            "order/main.c": "int zuser(void);\nint main(void) { return zuser() == 42 ? 0 : 1; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="order"))
    assert result.exit_code == 0, result.stderr_bytes.decode()


NESTED = {
    "nested/static.c": "int nested_static(void) { return 40; }\n",
    "nested/shared.c": "int nested_shared(void) { return 2; }\n",
    "nested/main.c": "int nested_static(void);\nint nested_shared(void);\n"
    "int main(void) { return nested_static() + nested_shared() == 42 ? 0 : 1; }\n",
}


def test_libraries_requiring_a_nested_working_directory(rule_runner: RuleRunner) -> None:
    # Paths and the run path are relative to `a/b/c`, where the test also runs.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="nested_static", sources=["nested/static.c"], cwd="a/b/c")\n'
        + 'toy_library(name="nested_shared", sources=["nested/shared.c"], shared=True, cwd="a/b/c")\n'
        + 'cc_test(name="nested", sources=["nested/main.c"], '
        'dependencies=[":nested_static", ":nested_shared"])\n',
        **NESTED,
    )
    result = _test(rule_runner, Address("", target_name="nested"))
    assert result.exit_code == 0, result.stderr_bytes.decode()
    built = rule_runner.request(
        executable.BuiltTest, [executable.TestBuildRequest(Address("", target_name="nested"))]
    )
    assert built.cwd == "a/b/c"


def test_a_required_and_an_unset_working_directory(rule_runner: RuleRunner) -> None:
    # Unset `cwd` takes whatever the other library requires.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="nested_static", sources=["nested/static.c"])\n'
        + 'toy_library(name="nested_shared", sources=["nested/shared.c"], shared=True, cwd="a")\n'
        + 'cc_test(name="nested", sources=["nested/main.c"], '
        'dependencies=[":nested_static", ":nested_shared"])\n',
        **NESTED,
    )
    result = _test(rule_runner, Address("", target_name="nested"))
    assert result.exit_code == 0, result.stderr_bytes.decode()
    built = rule_runner.request(
        executable.BuiltTest, [executable.TestBuildRequest(Address("", target_name="nested"))]
    )
    assert built.cwd == "a"


@pytest.mark.parametrize(
    "first, second, listed",
    [('"a"', '"b"', "['//:first: a', '//:second: b']"), ('""', '"a"', "['//:first: .', '//:second: a']")],
)
def test_libraries_requiring_different_working_directories(
    rule_runner: RuleRunner, first: str, second: str, listed: str
) -> None:
    # `""` requires the sandbox root, which conflicts like any other directory.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + f'toy_library(name="first", sources=["nested/static.c"], cwd={first})\n'
        + f'toy_library(name="second", sources=["nested/shared.c"], cwd={second})\n'
        + 'cc_test(name="nested", sources=["nested/main.c"], dependencies=[":first", ":second"])\n',
        **NESTED,
    )
    result = _test(rule_runner, Address("", target_name="nested"))
    assert result.exit_code == 1
    assert (
        f"//:nested: the libraries need different working directories: {listed}"
        in result.stderr_bytes.decode()
    )


def test_library_failing_to_say_where_is_a_failed_test(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="absolute", sources=["nested/static.c"], cwd="/abs")\n'
        + 'cc_test(name="nested", sources=["nested/main.c"], dependencies=[":absolute"])\n',
        **NESTED,
    )
    result = _test(rule_runner, Address("", target_name="nested"))
    assert result.exit_code == 1
    assert (
        "//:nested: //:absolute: `cwd` is relative to the sandbox root, not `/abs`"
        in result.stderr_bytes.decode()
    )


@pytest.mark.parametrize("output_dir", [None, "out"])
def test_libraries_writing_one_path(rule_runner: RuleRunner, output_dir: str | None) -> None:
    # Same-named libraries in two directories build apart; forced into one directory, they
    # clash, which fails the test.
    field = f', output_dir="{output_dir}"' if output_dir else ""
    _write_project(
        rule_runner,
        BUILD=BUILD + 'cc_test(name="both", sources=["both.c"], dependencies=["//p:lib", "//q:lib"])\n',
        **{
            "p/BUILD": f'toy_library(name="lib", sources=["p.c"]{field})\n',
            "q/BUILD": f'toy_library(name="lib", sources=["q.c"]{field})\n',
            "p/p.c": "int p(void) { return 40; }\n",
            "q/q.c": "int q(void) { return 2; }\n",
            "both.c": "int p(void);\nint q(void);\nint main(void) { return p() + q() == 42 ? 0 : 1; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="both"))
    if output_dir is None:
        assert result.exit_code == 0, result.stderr_bytes.decode()
    else:
        assert result.exit_code == 1
        assert (
            "//:both: the libraries ['p:lib', 'q:lib'] put different libraries at `out/liblib.a`"
            in result.stderr_bytes.decode()
        )


def test_a_library_and_its_file_where_another_has_a_directory(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="x", sources=["nested/static.c"], output_dir="out")\n'
        + 'toy_library(name="y", sources=["nested/shared.c"], output_dir="out/libx.a")\n'
        + 'cc_test(name="nested", sources=["nested/main.c"], dependencies=[":x", ":y"])\n',
        **NESTED,
    )
    result = _test(rule_runner, Address("", target_name="nested"))
    assert result.exit_code == 1
    assert (
        "//:nested: the libraries ['//:x', '//:y'] put different libraries at `out/libx.a`"
        in result.stderr_bytes.decode()
    )


def test_each_language_asks_for_its_own_interface(rule_runner: RuleRunner) -> None:
    # The C unit of a C++ test needs the library's C interface, which this library lacks.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="cxx_only", sources=["demo.c"], languages=["cxx"])\n'
        + 'cc_test(name="mixed", sources=["mixed/main.cpp", "mixed/util.c"], dependencies=[":cxx_only"])\n',
        **{
            "mixed/main.cpp": 'extern "C" int util(void);\nint main() { return util(); }\n',
            "mixed/util.c": "int util(void) { return 0; }\n",
        },
    )
    result = _test(rule_runner, Address("", target_name="mixed"))
    assert result.exit_code == 1
    assert "//:mixed: //:cxx_only has no c consumers" in result.stderr_bytes.decode()
