"""Per-translation-unit header scan: the exact headers a compile reads. A scan that fails, or
that names an include no input provides, is an error: a silent fallback would hide the missing
input until the compile fails with a confusing message."""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from pants.engine.fs import Digest, MergeDigests
from pants.engine.intrinsics import execute_process, merge_digests
from pants.engine.process import Process
from pants.engine.rules import implicitly
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

HEADER_SUFFIXES = (".h", ".hpp", ".hh", ".hxx", ".inc", ".inl", ".ipp", ".tcc", ".ixx", ".def")


class ScanError(Exception):
    pass


def is_header_like(path: str) -> bool:
    base = os.path.basename(path)
    return "." not in base or base.endswith(HEADER_SUFFIXES)


def without_outputs(tokens: Sequence[str], drop: Collection[str] = ()) -> list[str]:
    """A compile command without its compile-only, output and depfile options, nor the tokens
    in `drop`."""
    kept: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in ("-o", "-MT", "-MF", "-MQ"):
            skip = True
            continue
        if token in ("-c", "-MD", "-MMD") or token in drop:
            continue
        kept.append(token)
    return kept


def scan_argv(tokens: Sequence[str]) -> list[str]:
    """A compile command with its output and depfile options replaced by `-M -MG`."""
    return [*without_outputs(tokens), "-M", "-MG"]


_DEPFILE_TOKEN = re.compile(r"(?:\\.|[^\s\\])+")


def depfile_entries(text: str, cwd: str) -> list[tuple[str, str]]:
    """`(as written, sandbox-root-relative)` for every dependency in Make-style output, which the
    compiler writes relative to its working directory; absolute paths dropped. A header `-MG`
    could not find is listed as spelled in the `#include`."""
    body = text.replace("\\\r\n", " ").replace("\\\n", " ")
    _, _, rest = body.partition(":")
    entries = []
    for token in _DEPFILE_TOKEN.findall(rest):
        # gcc escapes a space or `#` with a backslash and `$` as `$$`.
        token = re.sub(r"\\([ #])", r"\1", token).replace("$$", "$")
        if os.path.isabs(token):
            continue
        entries.append((token, os.path.normpath(os.path.join(cwd, token))))
    return entries


def parse_depfile(text: str, cwd: str) -> list[str]:
    """Sandbox-root-relative paths from Make-style dependency output; absolute paths dropped."""
    return [path for _, path in depfile_entries(text, cwd)]


@dataclass(frozen=True)
class HeaderUniverse:
    """What a scan runs against besides the translation unit's own files."""

    digest: Digest
    """The files besides the translation unit's own that a scan may read."""
    paths: tuple[str, ...]
    """Every path an include may resolve to."""
    cwd: str
    """The compiles' working directory, relative to the sandbox root."""
    env: FrozenDict[str, str]
    """The compiles' environment."""


async def scan_headers(
    universe: HeaderUniverse,
    owner: str,
    argv: Sequence[str],
    what: str,
    files: Iterable[Digest],
    file_paths: Iterable[str],
    sources: Iterable[str],
) -> tuple[str, ...]:
    """Scan the compile `argv`, with the translation unit's own `files` next to the universe;
    the sandbox-root-relative headers it found, minus `sources`. Fails on a non-zero exit or an
    include outside the universe and `file_paths`, naming the include as the source spells it."""
    input_digest = await merge_digests(MergeDigests([*files, universe.digest]))
    result = await execute_process(
        Process(
            argv=scan_argv(argv),
            description=f"Scan {what} ({owner})",
            input_digest=input_digest,
            working_directory=universe.cwd,
            env=universe.env,
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        raise ScanError(
            f"{owner}: header scan of {what} failed (exit {result.exit_code}):\n"
            f"{result.stderr.decode(errors='replace')}"
        )
    entries = depfile_entries(result.stdout.decode(), universe.cwd)
    known = {*universe.paths, *file_paths}
    unknown = sorted({spelled for spelled, path in entries if path not in known})
    if unknown:
        raise ScanError(
            f"{owner}: {what} includes files that none of its inputs provide: {unknown}. Add the "
            "files to a target it depends on, or if the build generates them, check them in or "
            "generate them with a codegen dependency."
        )
    return tuple(sorted({path for _, path in entries} - set(sources)))
