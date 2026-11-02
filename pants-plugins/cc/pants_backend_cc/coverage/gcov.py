"""gcov and gcov-tool of the host toolchain's gcc, which must be its own version to read its notes.
`pants test --use-coverage` looks for them before it builds anything, so a host without them fails
at once rather than after every test has run.

gcov resolves an object's sources from the working directory its compile ran in, which every note
records as the absolute path of a sandbox that is gone. Left to itself gcovr looks for that
directory by trying the root and then each parent of the object, running gcov once per attempt.
The gcov it runs here is `WRAPPER`, a Python script on the interpreter gcovr's pex runs on (so
`GCOV` is written where that pex is known, `rules.py`): it reads the directory from the file the
compile's producer wrote next to the notes (`data.COMPILE_DIRECTORY`), runs gcov there once, in
the text format on its stdout, and writes the `.gcov` files where gcovr looks for them. gcov 14
and newer offer a JSON format, which gcovr asks for when it sees it; the wrapper drops the
request, as that format names sources from the recorded directory, and gcovr reads text wherever
gcov writes it. gcovr runs gcov for one object at a time per directory it runs it in, so `rules.py`
has it run in each object's own directory (`--gcov-object-directory=.`) rather than the root.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import BashBinary, BinaryPath, find_binary
from pants.engine.fs import FileContent
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_cc.coverage.data import COMPILE_DIRECTORY
from pants_backend_cc.util_rules.toolchain import (
    CcToolchainRequest,
    binary_request,
    resolve_cc_toolchain,
)
from pants_backend_platforms.util_rules.platforms import KnownPlatforms

GCOV = "__gcov/gcov"
"""Where the gcov gcovr runs is."""
WRAPPER = "__gcov/gcov.py"
"""The Python script `GCOV` runs."""


class GcovError(Exception):
    pass


def sibling_tool(compiler: str, tool: str) -> str | None:
    """`tool` (`gcov`, `gcov-tool`) next to a gcc, with `gcc` in its name replaced:
    `/usr/bin/x86_64-linux-gnu-gcc-13` has `/usr/bin/x86_64-linux-gnu-gcov-13`."""
    directory, name = os.path.split(compiler)
    index = name.rfind("gcc")
    if index < 0:
        return None
    return os.path.join(directory, f"{name[:index]}{tool}{name[index + 3 :]}")


def major_version(output: str) -> str | None:
    """The major version ending the first line of a `--version` banner, or of `-dumpversion`."""
    lines = output.strip().splitlines()
    match = re.search(r"(\d+)(\.\d+)*$", lines[0].strip()) if lines else None
    return match.group(1) if match else None


_WRAPPER = '''\
"""gcov for gcovr, run in the directory the object was compiled in, which the compile's producer
named next to its notes. gcov writes to this script instead of to files (`--stdout`), and the
script writes one `.gcov` file per source where gcovr runs it, named as gcov would name it, with
the source named from there, and reports it as gcov would (`Creating '...'`): gcovr may run it for
objects of different directories at once, and the compile directory is shared. Arguments: the
gcov to run, then gcovr's arguments for it (`--help` and `--version` pass through)."""

import hashlib
import os
import re
import subprocess
import sys

SOURCE = re.compile(r"^(\\s*-:\\s*0:Source:)(.*)$")

gcov, args = sys.argv[1], sys.argv[2:]
datafiles = [a for a in args if a.endswith((".gcda", ".gcno"))]
if not datafiles:
    os.execv(gcov, [gcov, *args])
(data,) = datafiles
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
stem = data[: data.rfind(".")]
try:
    with open(stem + "{suffix}") as f:
        directory = os.path.join(root, f.read())
except FileNotFoundError:
    sys.exit(f"{{stem}}{suffix} is missing: whoever compiled {{stem}}.o did not name its directory")
if "--object-directory" in args:
    index = args.index("--object-directory") + 1
    args[index] = os.path.abspath(args[index])
# gcovr asks gcov 14 and newer for the JSON format, which names sources from the recorded
# directory, a sandbox that is gone; the text format opens them from gcov's own directory.
args = [a for a in args if a not in ("--json-format", "--conditions")]

run = subprocess.run(
    [gcov, "--stdout", *args],
    cwd=directory,
    stdout=subprocess.PIPE,
    text=True,
    errors="surrogateescape",
)
files = []
for line in run.stdout.splitlines(keepends=True):
    match = SOURCE.match(line)
    if match:
        source = match.group(2)
        if not os.path.isabs(source):
            source = os.path.relpath(os.path.join(directory, source), os.getcwd())
        line = match.group(1) + source + "\\n"
        files.append((source, []))
    if files:
        files[-1][1].append(line)
for source, lines in files:
    digest = hashlib.md5(source.encode("utf-8", errors="surrogateescape")).hexdigest()
    name = f"{{os.path.basename(source)}}##{{digest}}.gcov"
    with open(name, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.writelines(lines)
    print(f"Creating '{{name}}'")
sys.exit(run.returncode)
'''


def wrapper() -> str:
    return _WRAPPER.format(suffix=COMPILE_DIRECTORY)


def shim(bash: str, python: str, gcov: str) -> str:
    """`GCOV`: `WRAPPER` next to it, on `python` without its site, over `gcov`."""
    return f'#!{bash}\nexec {shlex.quote(python)} -I -S "$(dirname "$0")/gcov.py" {shlex.quote(gcov)} "$@"\n'


@dataclass(frozen=True)
class GcovTools:
    gcov: str
    gcov_tool: str
    bash: str
    fingerprint: str
    """Changes with either tool, whose output differs between versions while the paths may not."""

    def files(self, python: str) -> list[FileContent]:
        """`GCOV` and `WRAPPER`, for gcovr's sandbox."""
        return [
            FileContent(GCOV, shim(self.bash, python, self.gcov).encode(), is_executable=True),
            FileContent(WRAPPER, wrapper().encode()),
        ]


async def _find(compiler: str, tool: str, search_path: tuple[str, ...]) -> BinaryPath | None:
    sibling = sibling_tool(compiler, tool)
    for request in (
        *([binary_request(sibling, (), version=True)] if sibling else []),
        binary_request(tool, search_path, version=True),
    ):
        found = await find_binary(request, **implicitly())
        if found.first_path is not None:
            return found.first_path
    return None


@rule(desc="Find gcov", level=LogLevel.DEBUG)
async def find_gcov_tools(known: KnownPlatforms, bash: BashBinary) -> GcovTools:
    toolchain = await resolve_cc_toolchain(CcToolchainRequest(known.host), **implicitly())
    compiler = toolchain.cc.path
    gcov, gcov_tool = await concurrently(
        _find(compiler, "gcov", toolchain.search_path),
        _find(compiler, "gcov-tool", toolchain.search_path),
    )
    if gcov is None or gcov_tool is None:
        missing = [name for name, path in (("gcov", gcov), ("gcov-tool", gcov_tool)) if path is None]
        raise GcovError(
            f"C/C++ coverage needs the gcov and gcov-tool of the host compiler `{compiler}`; found "
            f"no {' or '.join(missing)} next to it or on the search path "
            f"({os.pathsep.join(toolchain.search_path)})."
        )
    tools = (compiler, gcov.path, gcov_tool.path)
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(
                    bash.path,
                    "-c",
                    '"$1" -dumpversion && echo --- && "$2" --version && echo --- && "$3" --version',
                    "probe",
                    *tools,
                ),
                env={
                    "__PANTS_GCOV": ",".join(
                        (toolchain.cc.fingerprint, gcov.fingerprint, gcov_tool.fingerprint)
                    )
                },
                description=f"Check the versions of {gcov.path} and {gcov_tool.path}",
                level=LogLevel.DEBUG,
            )
        )
    )
    versions = [major_version(o) for o in result.stdout.decode().split("---\n")]
    if len(set(versions)) != 1 or versions[0] is None:
        found = ", ".join(f"`{t}` {v or 'unknown'}" for t, v in zip(tools, versions))
        raise GcovError(
            f"C/C++ coverage reads the notes of the host compiler `{compiler}` with the gcov and "
            f"gcov-tool of its version, which must be gcc's; found {found}."
        )
    return GcovTools(gcov.path, gcov_tool.path, bash.path, f"{gcov.fingerprint},{gcov_tool.fingerprint}")


def rules():
    return collect_rules()
