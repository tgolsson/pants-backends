"""Make a configured tree independent of the sandbox it was configured in.

CMake bakes the absolute configure directory into `build.ninja` (source paths, include flags,
`cmake_ninja_workdir`), `rules.ninja` (the regenerate rule), `compile_commands.json` and every
file it generates, and records the absolute paths of its own binaries and of ninja. Every such
path is rewritten: the sandbox root to `PLACEHOLDER`, the tool paths (read exactly from
`CMakeCache.txt`, not pattern-matched) to the bare tool name. The `RERUN_CMAKE` edge, whose
inputs are the CMake module tree, is dropped, together with the "a missing CMake input file is
not an error" phony edge that lists the same inputs. What remains has the same digest wherever
it was produced.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

PLACEHOLDER = "__PANTS_CMAKE_ROOT__"

_WORKDIR_RE = re.compile(r"^cmake_ninja_workdir = (.+?)/?$", re.MULTILINE)
_RERUN_EDGE_RE = re.compile(r"^build ([^\n]*): RERUN_CMAKE([^\n]*)\n(?:[ \t][^\n]*\n)*", re.MULTILINE)
_INPUTLESS_PHONY_RE = re.compile(r"^build ([^\n]+): phony[ \t]*\n", re.MULTILINE)
_CACHE_TOOL_RE = re.compile(
    r"^(CMAKE_COMMAND|CMAKE_CTEST_COMMAND|CMAKE_CPACK_COMMAND|CMAKE_EDIT_COMMAND|CMAKE_MAKE_PROGRAM)"
    r":(?:INTERNAL|FILEPATH)=(.+)$",
    re.MULTILINE,
)
_CACHE_TOOL_NAMES = {
    "CMAKE_COMMAND": "cmake",
    "CMAKE_CTEST_COMMAND": "ctest",
    "CMAKE_CPACK_COMMAND": "cpack",
    "CMAKE_EDIT_COMMAND": "ccmake",
    "CMAKE_MAKE_PROGRAM": "ninja",
}


class CanonicalizeError(Exception):
    pass


def sandbox_root(build_ninja: str, build_dir: str) -> str:
    """The absolute directory configure ran in, from `cmake_ninja_workdir` minus `build_dir`."""
    m = _WORKDIR_RE.search(build_ninja)
    if not m:
        raise CanonicalizeError("build.ninja has no cmake_ninja_workdir")
    workdir = m.group(1)
    suffix = "/" + build_dir.strip("/")
    if build_dir.strip("/") in ("", "."):
        raise CanonicalizeError("the build directory must be a subdirectory of the project")
    if not workdir.endswith(suffix):
        raise CanonicalizeError(f"cmake_ninja_workdir `{workdir}` does not end in `{suffix}`")
    root = workdir[: -len(suffix)]
    if not root:
        raise CanonicalizeError(f"cmake_ninja_workdir `{workdir}` has no sandbox root")
    return root


def tool_paths(cmake_cache: str) -> dict[str, str]:
    """Absolute tool path to bare name, from the cache entries CMake writes for its own tools."""
    found = {}
    for m in _CACHE_TOOL_RE.finditer(cmake_cache):
        path = m.group(2).strip()
        if path and not path.endswith("-NOTFOUND"):
            found[path] = _CACHE_TOOL_NAMES[m.group(1)]
    return found


def canonicalize(text: str, root: str, tools: Mapping[str, str]) -> str:
    if not root:
        raise CanonicalizeError("empty sandbox root")
    for path in sorted(tools, key=len, reverse=True):
        text = text.replace(path, tools[path])
    text = text.replace(root, PLACEHOLDER)
    if root in text:
        raise CanonicalizeError("sandbox path survived canonicalization")
    return text


def canonicalize_build_ninja(text: str, root: str, tools: Mapping[str, str]) -> str:
    rerun_inputs: set[str] = set()

    def drop_rerun(m: re.Match) -> str:
        rerun_inputs.update(t for t in m.group(2).split() if t not in ("|", "||"))
        return ""

    text = _RERUN_EDGE_RE.sub(drop_rerun, text)
    if rerun_inputs:

        def drop_missing_input_phony(m: re.Match) -> str:
            outputs = {t for t in m.group(1).split() if t != "|"}
            return "" if outputs <= rerun_inputs else m.group(0)

        text = _INPUTLESS_PHONY_RE.sub(drop_missing_input_phony, text)
    return canonicalize(text, root, tools)


def rebase(text: str, relative_root: str) -> str:
    """Replace the placeholder with the path of the source root relative to the build dir."""
    return text.replace(PLACEHOLDER, relative_root)
