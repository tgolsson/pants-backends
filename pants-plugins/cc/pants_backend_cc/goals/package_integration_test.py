"""`cc_binary` with the host compiler against `toy_library`, packaged and then run outside
Pants."""

from __future__ import annotations

import os
import re
import subprocess

import pytest
from pants.core.goals import package as core_package
from pants.core.goals.package import BuiltPackage
from pants.core.util_rules import source_files, system_binaries
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.source import source_root
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cc import register
from pants_backend_cc.goals.package import CcBinaryFieldSet
from pants_backend_cc.testutil import GenTarget, codegen_rules
from pants_backend_cc.toy_library import ToyLibraryTarget, toy_library_rules

BUILD = """\
toy_library(name="demo", sources=["demo.c", "demo.h"])

cc_binary(
    name="app",
    sources=["app/main.c", "app/extra/scale.h"],
    dependencies=[":demo"],
    include_dirs=["app/extra"],
    defines=["SCALE=2"],
    compiler_args=["-Wall", "-Werror"],
    linker_args=["-lm"],
)
"""

FILES = {
    "BUILD": BUILD,
    "demo.c": "int demo(void) { return 42; }\n",
    "demo.h": "int demo(void);\n",
    "app/extra/scale.h": "#define BASE 21\n",
    "app/main.c": '#include <math.h>\n#include <stdio.h>\n#include "demo.h"\n#include "scale.h"\n'
    'int main(int argc, char **argv) { printf("%d %d\\n", argc, (int)sqrt((double)(BASE * SCALE))); '
    "return demo() == 42 ? 0 : 1; }\n",
}


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *source_files.rules(),
            *system_binaries.rules(),
            *core_package.rules(),
            *source_root.rules(),
            *codegen_rules(),
            *toy_library_rules(),
            *register.rules(),
            QueryRule(BuiltPackage, [CcBinaryFieldSet]),
        ],
        target_types=[*register.target_types(), GenTarget, ToyLibraryTarget],
    )


def _write_project(rule_runner: RuleRunner, **overrides: str) -> None:
    rule_runner.write_files({**FILES, **overrides})
    rule_runner.set_options([], env_inherit={"PATH"})


def _package(rule_runner: RuleRunner, address: Address) -> BuiltPackage:
    target = rule_runner.get_target(address)
    return rule_runner.request(BuiltPackage, [CcBinaryFieldSet.create(target)])


def _run(rule_runner: RuleRunner, package: BuiltPackage, *args: str) -> subprocess.CompletedProcess:
    rule_runner.write_digest(package.digest, path_prefix="dist")
    (artifact,) = package.artifacts
    assert artifact.relpath is not None
    path = os.path.join(rule_runner.build_root, "dist", artifact.relpath)
    return subprocess.run([path, *args], capture_output=True, text=True)


def test_package_and_run(rule_runner: RuleRunner) -> None:
    _write_project(rule_runner)
    package = _package(rule_runner, Address("", target_name="app"))
    # The executable alone, at the default `output_path`, executable.
    contents = rule_runner.request(DigestContents, [package.digest])
    assert [(fc.path, fc.is_executable) for fc in contents] == [("app", True)]
    result = _run(rule_runner, package, "x")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "2 6\n"


def test_output_path_in_a_subdirectory(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        **{
            "BUILD": 'toy_library(name="demo", sources=["demo.c", "demo.h"])\n',
            "tools/bin/BUILD": 'cc_binary(name="tool", sources=["tool.c"], dependencies=["//:demo"])\n',
            "tools/bin/tool.c": '#include "demo.h"\nint main(void) { return demo() == 42 ? 0 : 1; }\n',
        },
    )
    # Pants' default `output_path`: the spec path with dots for slashes, then the name.
    default = _package(rule_runner, Address("tools/bin", target_name="tool"))
    assert [a.relpath for a in default.artifacts] == ["tools.bin/tool"]
    assert _run(rule_runner, default).returncode == 0

    rule_runner.write_files(
        {
            "tools/bin/BUILD": 'cc_binary(name="tool", sources=["tool.c"], dependencies=["//:demo"], '
            'output_path="bin/renamed")\n'
        }
    )
    renamed = _package(rule_runner, Address("tools/bin", target_name="tool"))
    assert [a.relpath for a in renamed.artifacts] == ["bin/renamed"]


def test_library_with_a_working_directory(rule_runner: RuleRunner) -> None:
    # The executable is built under the library's `cwd`; the package is still just it.
    _write_project(
        rule_runner,
        BUILD=BUILD + 'toy_library(name="nested", sources=["nested/value.c"], cwd="sub")\n'
        'cc_binary(name="in_sub", sources=["nested/main.c"], dependencies=[":nested"])\n',
        **{
            "nested/value.c": "int value(void) { return 42; }\n",
            "nested/main.c": "int value(void);\nint main(void) { return value() == 42 ? 0 : 1; }\n",
        },
    )
    package = _package(rule_runner, Address("", target_name="in_sub"))
    contents = rule_runner.request(DigestContents, [package.digest])
    assert [(fc.path, fc.is_executable) for fc in contents] == [("in_sub", True)]
    result = _run(rule_runner, package)
    assert result.returncode == 0, result.stderr


def test_cxx_translation_unit(rule_runner: RuleRunner) -> None:
    _write_project(
        rule_runner,
        BUILD=BUILD + 'cc_binary(name="cxx", sources=["cxx/main.cpp"], dependencies=[":demo"])\n',
        **{
            "cxx/main.cpp": '#include <string>\nextern "C" {\n#include "demo.h"\n}\n'
            'int main() { return std::string("forty-two").size() + 33 == (unsigned)demo() ? 0 : 1; }\n'
        },
    )
    result = _run(rule_runner, _package(rule_runner, Address("", target_name="cxx")))
    assert result.returncode == 0, result.stderr


def test_compiles_sources_of_dependencies(rule_runner: RuleRunner) -> None:
    # The binary's translation units may all come from `dependencies`.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'test_gen(name="main", sources=["gen/main.c.in"])\n'
        + 'cc_binary(name="generated", dependencies=[":demo", ":main"])\n',
        **{"gen/main.c.in": '#include "demo.h"\nint main(void) { return demo() == 42 ? 0 : 1; }\n'},
    )
    result = _run(rule_runner, _package(rule_runner, Address("", target_name="generated")))
    assert result.returncode == 0, result.stderr


def test_libraries_link_in_declared_not_sorted_order(rule_runner: RuleRunner) -> None:
    # `zuser` needs `aprov`, so only the declared order links.
    _write_project(
        rule_runner,
        BUILD=BUILD
        + 'toy_library(name="zuser", sources=["order/zuser.c"])\n'
        + 'toy_library(name="aprov", sources=["order/aprov.c"])\n'
        + 'cc_binary(name="order", sources=["order/main.c"], dependencies=[":zuser", ":aprov"])\n',
        **{
            "order/zuser.c": "int aprov(void);\nint zuser(void) { return aprov() + 2; }\n",
            "order/aprov.c": "int aprov(void) { return 40; }\n",
            "order/main.c": "int zuser(void);\nint main(void) { return zuser() == 42 ? 0 : 1; }\n",
        },
    )
    result = _run(rule_runner, _package(rule_runner, Address("", target_name="order")))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "build, files, message",
    [
        (
            'cc_binary(name="broken", sources=["alone.c"])\n',
            {"alone.c": "int main(void) { return 0; }\n"},
            "//:broken: `dependencies` must name at least one library",
        ),
        (
            'cc_binary(name="broken", dependencies=[":demo"])\n',
            {},
            "//:broken: no translation unit to compile in `sources` or `dependencies`",
        ),
        (
            'toy_library(name="c_only", sources=["demo.c"], languages=["c"])\n'
            'cc_binary(name="broken", sources=["cxx/main.cpp"], dependencies=[":c_only"])\n',
            {"cxx/main.cpp": "int main() { return 0; }\n"},
            "//:broken: //:c_only has no cxx consumers: its `languages` are ['c']",
        ),
        (
            'cc_binary(name="broken", sources=["missing.c"], dependencies=[":demo"])\n',
            {"missing.c": '#include "nowhere.h"\nint main(void) { return 0; }\n'},
            "//:broken: missing.c includes files that none of its inputs provide: ['nowhere.h']",
        ),
    ],
)
def test_build_errors(rule_runner: RuleRunner, build: str, files: dict[str, str], message: str) -> None:
    _write_project(rule_runner, BUILD=BUILD + build, **files)
    with pytest.raises(ExecutionError, match=re.escape(message)):
        _package(rule_runner, Address("", target_name="broken"))
