"""gcov and gcov-tool of the host toolchain's gcc, which must be its own version to read its notes.
`pants test --use-coverage` looks for them before it builds anything, so a host without them fails
at once rather than after every test has run.

gcovr runs gcov from a directory it finds by trying the root and the `.gcda`'s parent
directories until the sources resolve, which works whatever sandbox the compile ran in. gcov's
JSON format, which gcovr prefers from gcc 14 on, names sources from the compile's recorded working
directory instead, a sandbox that is gone; so gcovr gets a gcov that drops `--json-format` (and
`--conditions`, which only that format reports), and reads the text format.
"""

from __future__ import annotations

import os
import re
import shlex
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
"""Where `GcovTools.wrapper` puts the gcov gcovr runs."""

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


@dataclass(frozen=True)
class GcovTools:
    wrapper: Digest
    """`gcov_wrapper` at `GCOV`."""
    gcov_tool: str
    fingerprint: str
    """Changes with either tool, whose output differs between versions while the paths may not."""


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
    return GcovTools(wrapper, gcov_tool.path, f"{gcov.fingerprint},{gcov_tool.fingerprint}")


def rules():
    return collect_rules()
