"""`cpp_generate` through the real clang-format from the default lockfile."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from pants.backend.cc.target_types import CCSourceField
from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals import lint_goal
from pants.core.util_rules import config_files, source_files
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.rules import QueryRule
from pants.engine.target import HydratedSources, HydrateSourcesRequest
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_cc.generate import register
from pants_backend_generate import register as generate_register
from pants_backend_generate.goals.generate import Generate
from pants_backend_generate.target_types import GenerateSourcesField

CPPGEN_PY = dedent(
    """\
    import os

    os.makedirs("cpp/out", exist_ok=True)
    with open("cpp/out/answer.cpp", "w") as f:
        f.write("int   answer( ){return 42;}\\n")
    """
)

ENV = {"PATH", "PYENV_ROOT", "HOME"}


def test_cpp_generate_is_clang_formatted() -> None:
    runner = PythonRuleRunner(
        rules=[
            *python_register.rules(),
            *core_target_types.rules(),
            *lint_goal.rules(),
            *config_files.rules(),
            *source_files.rules(),
            *generate_register.rules(),
            *register.rules(),
            QueryRule(HydratedSources, [HydrateSourcesRequest]),
        ],
        target_types=[
            *generate_register.target_types(),
            *register.target_types(),
            *python_register.target_types(),
        ],
    )
    runner.write_files(
        {
            ".clang-format": "BasedOnStyle: LLVM\n",
            "tools/cppgen.py": CPPGEN_PY,
            "tools/BUILD": 'python_sources()\npex_binary(name="cppgen", entry_point="cppgen.py")\n',
            "cpp/BUILD": 'cpp_generate(name="gen", runnable="//tools:cppgen", sources=[], '
            'outputs=["cpp/out/answer.cpp"])\n',
        }
    )
    runner.set_options([], env_inherit=ENV)

    result = runner.run_goal_rule(Generate, args=["cpp:gen"], env_inherit=ENV)
    assert result.exit_code == 0, result.stderr
    assert Path(runner.build_root, "cpp/out/answer.cpp").read_text() == "int answer() { return 42; }\n"
    # Consumers asking for C/C++ sources get the formatted bytes too.
    target = runner.get_target(Address("cpp", target_name="gen"))
    hydrated = runner.request(
        HydratedSources,
        [
            HydrateSourcesRequest(
                target[GenerateSourcesField], for_sources_types=(CCSourceField,), enable_codegen=True
            )
        ],
    )
    contents = runner.request(DigestContents, [hydrated.snapshot.digest])
    assert {fc.path: fc.content for fc in contents} == {
        "cpp/out/answer.cpp": b"int answer() { return 42; }\n"
    }
