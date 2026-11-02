"""gcov and gcov-tool of the host toolchain's gcc, which must be its own version to read its notes.
`pants test --use-coverage` looks for them before it builds anything, so a host without them fails
at once rather than after every test has run.

gcov resolves an object's sources from the working directory its compile ran in, which every note
records as the absolute path of a sandbox that is gone. gcovr finds that directory by trying the
root and then each parent of the object, running gcov once per attempt (`gcov_wrapper`, which
drops the JSON options so that gcovr settles for the text format wherever gcov advertises both:
the JSON format reports the recorded directory and resolves nothing).

`SMART_WRAPPER`, the default, reads the directory off the note instead (`compile_directory`), so
gcovr's first attempt succeeds and gcov runs once per object. With gcov 14 and newer, whose JSON
format gcovr asks for, it runs gcov on a scratch copy of the object's notes and data with the
recorded directory rewritten to this sandbox's (`patch_directory`); with older gcov it runs the
text format from that directory and moves the `.gcov` files back to where gcovr looks for them.
It runs on the interpreter gcovr's pex runs on, so `GCOV` is written where that pex is known
(`rules.py`).
"""

from __future__ import annotations

import inspect
import os
import re
import shlex
import struct
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import BashBinary, BinaryPath, find_binary
from pants.engine.fs import CreateDigest, Digest, FileContent
from pants.engine.intrinsics import create_digest
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_cc.util_rules.toolchain import (
    CcToolchainRequest,
    binary_request,
    resolve_cc_toolchain,
)
from pants_backend_platforms.util_rules.platforms import KnownPlatforms

GCOV = "__gcov/gcov"
"""Where the gcov gcovr runs is."""
SMART_WRAPPER = "__gcov/gcov.py"
"""The Python wrapper `GCOV` runs unless `[gcovr].heuristic_object_directory`."""

_DROPPED = ("--json-format", "--conditions")


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


def gcov_wrapper(bash: str, gcov: str) -> str:
    """A script running `gcov` without the options of its JSON format."""
    dropped = "|".join(_DROPPED)
    return (
        f"#!{bash}\n"
        "args=()\n"
        f'for a in "$@"; do case "$a" in {dropped}) ;; *) args+=("$a") ;; esac; done\n'
        f'exec {shlex.quote(gcov)} "${{args[@]}}"\n'
    )


def recorded_directory(note: bytes) -> str:
    """The working directory a `.gcno` records. The header is the magic, the version, the stamp,
    the checksum, then the directory as a length-prefixed, NUL-padded string (gcc 9 and newer)."""
    if note[:4] != b"oncg":
        raise ValueError("not a gcov notes file")
    length = struct.unpack("<I", note[16:20])[0]
    return note[20 : 20 + length].rstrip(b"\0").decode()


def patch_directory(note: bytes, directory: str) -> bytes:
    """A `.gcno` with its recorded working directory replaced (see `recorded_directory`)."""
    if note[:4] != b"oncg":
        raise ValueError("not a gcov notes file")
    length = struct.unpack("<I", note[16:20])[0]
    encoded = directory.encode() + b"\0"
    encoded += b"\0" * (-len(encoded) % 4)
    return note[:16] + struct.pack("<I", len(encoded)) + encoded + note[20 + length :]


def compile_directory(recorded: str, note_path: str) -> str:
    """Where the compile ran, relative to the sandbox root: the longest lead of the note's
    directory that ends the recorded one. `/tmp/pants-sandbox-x/build` recorded for a note at
    `build/CMakeFiles/a.dir/x.cpp.gcno` is `build`; a note at `toybuild/x.c.gcno` recorded under
    `/tmp/pants-sandbox-x` was compiled at the root."""
    recorded_parts = recorded.rstrip("/").split("/")
    directory = os.path.dirname(note_path)
    parts = directory.split("/") if directory else []
    for k in range(len(parts), 0, -1):
        if parts[:k] == recorded_parts[-k:]:
            return "/".join(parts[:k])
    return ""


_SMART_WRAPPER = '''\
"""gcov for gcovr, with the directory the object was compiled in read off its notes. Arguments:
the gcov to run, then gcovr's arguments for it (`--help` and `--version` pass through)."""

import os
import shutil
import struct
import subprocess
import sys

{recorded_directory}

{patch_directory}

{compile_directory}

gcov, args = sys.argv[1], sys.argv[2:]
datafiles = [a for a in args if a.endswith((".gcda", ".gcno"))]
if not datafiles:
    os.execv(gcov, [gcov, *args])
(data,) = datafiles
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
stem = data[: data.rfind(".")]
with open(stem + ".gcno", "rb") as f:
    note = f.read()
directory = os.path.join(root, compile_directory(recorded_directory(note), os.path.relpath(stem, root)))
if "--object-directory" in args:
    index = args.index("--object-directory") + 1
    args[index] = os.path.abspath(args[index])

if "--json-format" in args:
    # The JSON names sources relative to the recorded directory: a scratch copy records this one.
    scratch = os.path.join(root, "__gcov", "notes", os.path.relpath(stem, root).replace(os.sep, "__"))
    os.makedirs(scratch, exist_ok=True)
    for suffix in (".gcno", ".gcda"):
        if os.path.exists(stem + suffix):
            shutil.copyfile(stem + suffix, os.path.join(scratch, os.path.basename(stem) + suffix))
    with open(os.path.join(scratch, os.path.basename(stem) + ".gcno"), "wb") as f:
        f.write(patch_directory(note, directory))
    args = [os.path.join(scratch, os.path.basename(data)) if a == data else a for a in args]
    if "--object-directory" in args:
        args[args.index("--object-directory") + 1] = scratch
    os.execv(gcov, [gcov, *args])

# The text format opens the sources from gcov's own directory, and gcovr reads the `.gcov` files
# gcov says it created from where it ran gcov, resolving their `Source:` line from there too: run
# gcov in the compile directory, then bring each file here with its source named from here.
run = subprocess.run([gcov, *args], cwd=directory, stdout=subprocess.PIPE, text=True)
for line in run.stdout.splitlines():
    if not (line.startswith("Creating '") and line.endswith("'")):
        continue
    created = line[len("Creating '") : -1]
    written = os.path.join(directory, created)
    if not os.path.exists(written):
        continue
    with open(written, encoding="utf-8", errors="surrogateescape") as f:
        lines = f.readlines()
    for i, text in enumerate(lines):
        if ":Source:" in text:
            head, source = text.rstrip("\\n").split(":Source:", 1)
            if not os.path.isabs(source):
                source = os.path.relpath(os.path.join(directory, source), os.getcwd())
            lines[i] = head + ":Source:" + source + "\\n"
            break
    with open(os.path.join(os.getcwd(), created), "w", encoding="utf-8", errors="surrogateescape") as f:
        f.writelines(lines)
    os.remove(written)
sys.stdout.write(run.stdout)
sys.exit(run.returncode)
'''


def smart_wrapper() -> str:
    return _SMART_WRAPPER.format(
        recorded_directory=inspect.getsource(recorded_directory),
        patch_directory=inspect.getsource(patch_directory),
        compile_directory=inspect.getsource(compile_directory),
    )


def smart_shim(bash: str, python: str, gcov: str) -> str:
    """`GCOV` running `SMART_WRAPPER` next to it with `python`."""
    return f'#!{bash}\nexec {shlex.quote(python)} "$(dirname "$0")/gcov.py" {shlex.quote(gcov)} "$@"\n'


@dataclass(frozen=True)
class GcovTools:
    wrapper: Digest
    """`GCOV` as `gcov_wrapper`: gcovr's own search for the compile directory."""
    gcov: str
    gcov_tool: str
    bash: str
    fingerprint: str
    """Changes with either tool, whose output differs between versions while the paths may not."""

    def smart_files(self, python: str) -> list[FileContent]:
        """`GCOV` as `smart_shim` with `SMART_WRAPPER`."""
        return [
            FileContent(GCOV, smart_shim(self.bash, python, self.gcov).encode(), is_executable=True),
            FileContent(SMART_WRAPPER, smart_wrapper().encode()),
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
    wrapper = await create_digest(
        CreateDigest([FileContent(GCOV, gcov_wrapper(bash.path, gcov.path).encode(), is_executable=True)])
    )
    return GcovTools(
        wrapper, gcov.path, gcov_tool.path, bash.path, f"{gcov.fingerprint},{gcov_tool.fingerprint}"
    )


def rules():
    return collect_rules()
