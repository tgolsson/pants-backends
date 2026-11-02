"""What a `cc_test` run under `--use-coverage` contributes to a report, how the `.gcda` files it
writes are matched to their notes, and what the compiles that wrote the notes leave for gcov.

gcc names an object's `.gcda` after the object's absolute path when it was compiled, inside that
compile's sandbox. `-fprofile-prefix-path` could strip the sandbox, but only from a command that
names it, which every library backend would then have to inject into its compiles. Instead the
test runs with `GCOV_PREFIX` in a directory it outputs, where each `.gcda` lands under its whole
compile-time path, and moves next to the `.gcno` whose path is the longest suffix of that path.

gcov resolves an object's sources from the directory its compile ran in, which the notes record
as a path in that sandbox. Whoever ran the compile knows that directory relative to the sandbox
root and writes it next to the notes (`compile_directories`), for the gcov gcovr runs (`gcov.py`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

from pants.core.goals.test import CoverageData, CoverageDataCollection
from pants.engine.addresses import Address
from pants.engine.fs import CreateDigest, Digest, FileContent, FileEntry, MergeDigests
from pants.engine.intrinsics import create_digest, get_digest_entries, merge_digests
from pants.engine.rules import concurrently

GCDA_DIR = "__pants_gcda"
"""Where a test writes its `.gcda` files, relative to its working directory."""

COMPILE_DIRECTORY = ".gcno.cwd"
"""The suffix of the file next to an object's notes (`x.c.gcno`, `x.c.gcno.cwd`) naming the
directory its compile ran in, relative to the sandbox root; "" is the root."""


def compile_directory_file(note: str) -> str:
    return f"{note[: -len('.gcno')]}{COMPILE_DIRECTORY}"


async def compile_directories(notes: Digest, directory: str) -> Digest:
    """Next to each `.gcno` in `notes`, the file naming `directory`, where all of their compiles
    ran. Every producer of a coverage digest (`CcLinkInterface.coverage`) merges this in."""
    entries = await get_digest_entries(notes)
    return await create_digest(
        CreateDigest(
            FileContent(compile_directory_file(e.path), directory.encode())
            for e in entries
            if isinstance(e, FileEntry) and e.path.endswith(".gcno")
        )
    )


@dataclass(frozen=True)
class CcCoverageData(CoverageData):
    address: Address
    digest: Digest
    """The `.gcno` notes, the `.gcda` data the run wrote next to them, and the files the compiles
    read, relative to the sandbox root."""


class CcCoverageDataCollection(CoverageDataCollection[CcCoverageData]):
    element_type = CcCoverageData


def gcda_destinations(written: Iterable[str], notes: Iterable[str]) -> dict[str, str]:
    """For each `.gcda` path under `GCDA_DIR`, the path next to its `.gcno`; one without notes
    (an object no library described) is left out."""
    known = set(notes)
    destinations = {}
    for path in written:
        parts = path.split("/")[1:]
        for start in range(len(parts)):
            candidate = "/".join(parts[start:])
            if f"{candidate[: -len('.gcda')]}.gcno" in known:
                destinations[path] = candidate
                break
    return destinations


async def _entries(digest: Digest) -> list[FileEntry]:
    return [e for e in await get_digest_entries(digest) if isinstance(e, FileEntry)]


async def coverage_data(address: Address, coverage: Digest, run_outputs: Digest) -> CcCoverageData:
    """`coverage` is `BuiltExecutable.coverage`; `run_outputs` the test process's outputs, `GCDA_DIR`
    relative to its working directory."""
    written, files = await concurrently(_entries(run_outputs), _entries(coverage))
    notes = [e.path for e in files if e.path.endswith(".gcno")]
    destinations = gcda_destinations((e.path for e in written if e.path.endswith(".gcda")), notes)
    moved = await create_digest(
        CreateDigest(
            FileEntry(destinations[e.path], e.file_digest) for e in written if e.path in destinations
        )
    )
    return CcCoverageData(address, await merge_digests(MergeDigests([coverage, moved])))


def gcov_prefix(cwd: str) -> str:
    """`GCOV_PREFIX` for a test running in `cwd`, relative to the sandbox root."""
    return "{chroot}/" + os.path.join(cwd, GCDA_DIR)
