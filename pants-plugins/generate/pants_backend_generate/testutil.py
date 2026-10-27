from __future__ import annotations

from collections.abc import Iterable
from textwrap import dedent

from pants.backend.python import register as python_register
from pants.core import target_types as core_target_types
from pants.core.goals import lint_goal
from pants.core.target_types import FilesGeneratorTarget, FileSourceField
from pants.core.util_rules import config_files, source_files
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.engine.target import HydratedSources, HydrateSourcesRequest, Target
from pants.testutil.python_rule_runner import PythonRuleRunner

from pants_backend_generate import register
from pants_backend_generate.target_types import GenerateSourcesField

GEN_PY = dedent(
    """\
    import argparse
    import os
    import subprocess
    import time

    parser = argparse.ArgumentParser()
    parser.add_argument("dir")
    parser.add_argument("--extra", action="store_true")
    parser.add_argument("--missing", action="store_true")
    parser.add_argument("--clobber", action="store_true")
    parser.add_argument("--nested", action="store_true")
    parser.add_argument("--data", action="store_true")
    parser.add_argument("--helper", action="store_true")
    parser.add_argument("--sleep", type=int, default=0)
    args = parser.parse_args()
    time.sleep(args.sleep)

    with open(os.path.join(args.dir, "input.txt")) as f:
        text = f.read().strip() + os.environ.get("GEN_SUFFIX", "")
    if args.data:
        with open(os.path.join(args.dir, "out", "data.txt")) as f:
            text += " " + f.read().strip()
    if args.helper:
        text += " " + subprocess.check_output(["helper"]).decode().strip()


    def write(name):
        with open(os.path.join(args.dir, name), "w") as f:
            f.write(f"{os.path.basename(name)}: {text}\\n")


    write("out/a.txt")
    if not args.missing:
        write("out/b.txt")
    if args.extra:
        write("out/c.txt")
    if args.clobber:
        write("out/notes.md")
    if args.nested:
        write("new/deep/x.txt")
    """
)

FILES = {
    "tools/gen.py": GEN_PY,
    "tools/helper.py": 'print("and helper")\n',
    "tools/BUILD": dedent(
        """\
        python_sources()
        pex_binary(name="gen", entry_point="gen.py")
        pex_binary(name="helper", entry_point="helper.py")
        """
    ),
    "proj/input.txt": "hello\n",
    # An input inside the output directory.
    "proj/out/notes.md": "not generated\n",
}


def generate_build(
    args: str = "", outputs: str = '"proj/out/a.txt", "proj/out/b.txt"', fields: str = ""
) -> str:
    return dedent(
        f"""\
        file_generate(
            name="gen",
            runnable="//tools:gen",
            args=["proj", {args}],
            sources=["input.txt", "out/notes.md"],
            outputs=[{outputs}],
            {fields}
        )
        """
    )


def rule_runner(*extra_rules: object, target_types: Iterable[type[Target]] = ()) -> PythonRuleRunner:
    return PythonRuleRunner(
        rules=[
            *extra_rules,
            *python_register.rules(),
            *core_target_types.rules(),
            *lint_goal.rules(),
            *config_files.rules(),
            *source_files.rules(),
            *register.rules(),
            QueryRule(HydratedSources, [HydrateSourcesRequest]),
        ],
        target_types=[
            *register.target_types(),
            *python_register.target_types(),
            FilesGeneratorTarget,
            *target_types,
        ],
    )


def write_project(runner: PythonRuleRunner, files: dict[str, str], *args: str) -> None:
    runner.write_files({**FILES, **files})
    runner.set_options(args, env_inherit={"PATH", "PYENV_ROOT", "HOME"})


def hydrate(runner: PythonRuleRunner, address: Address) -> HydratedSources:
    target = runner.get_target(address)
    return runner.request(
        HydratedSources,
        [
            HydrateSourcesRequest(
                target[GenerateSourcesField], for_sources_types=(FileSourceField,), enable_codegen=True
            )
        ],
    )
