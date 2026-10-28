# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""What scripted build steps share between runners: the Containerfile, picking outputs out of an
export, and the command line.

This module runs inside a sandboxed process, with the rules only deciding what to do, so it uses
only the standard library and never imports Pants.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fnmatch
import gzip
import json
import os
import re
import tarfile
from typing import Iterator

from pants_backend_oci.util_rules import oci_layout_builder

_GLOB = re.compile(r"[*?[]")
# Set by launching `oci-builder`, and naming host paths.
_LAUNCHER_ENV = frozenset({"PEX", "SCIE", "SCIE_ARGV0", "SCIE_BASE", "VIRTUAL_ENV"})


def host_env(drop: frozenset[str] = frozenset()) -> dict[str, str]:
    """This process's environment for the runner, without what launching `oci-builder` set or
    `drop`."""
    dropped = _LAUNCHER_ENV | drop
    return {k: v for k, v in os.environ.items() if k not in dropped and not k.startswith("__PEX_")}


def _parts(path: str) -> list[str]:
    return [part for part in path.removeprefix("./").strip("/").split("/") if part]


def copy_root(output: str) -> str:
    """The directory to copy out of the image for `output`: itself, or the part of a glob before
    its first wildcard, so the matches keep their paths and are picked out afterwards."""
    parts = _parts(output)
    static = next((i for i, part in enumerate(parts) if _GLOB.search(part)), len(parts))
    return "/" + "/".join(parts[:static])


def containerfile(base: str, run: list[str] | None, outputs: list[str]) -> str:
    """The build stage runs `run` as root in `base`, an image reference; `outputs` add a scratch
    stage holding only what they need."""
    lines = [f"FROM {base} AS build"]
    if run:
        lines += ["USER 0:0", f"RUN {json.dumps(run)}"]
    if outputs:
        lines.append("FROM scratch")
        roots = sorted({copy_root(output) for output in outputs})
        for root in roots:
            # A root inside another one is copied with it.
            if not any(root.startswith(other.rstrip("/") + "/") for other in roots if other != root):
                lines.append(f"COPY --from=build {json.dumps([root, root])}")
    return "\n".join(lines) + "\n"


def selected(name: str, outputs: list[str]) -> bool:
    """Whether `name` is an output or inside one. Globs match one path segment per segment."""
    parts = _parts(name)
    for output in outputs:
        pattern = _parts(output)
        if len(parts) >= len(pattern) and all(map(fnmatch.fnmatchcase, parts, pattern)):
            return True
    return False


def excluded(name: str, patterns: list[str]) -> bool:
    """Whether `name` or a directory it's in matches a pattern the way `tar --exclude` matches:
    against the `./`-prefixed path or any trailing part of it."""
    parts = _parts(name)
    for depth in range(1, len(parts) + 1):
        prefix = parts[:depth]
        candidates = ["./" + "/".join(prefix)] + ["/".join(prefix[i:]) for i in range(depth)]
        if any(fnmatch.fnmatchcase(c, p) for c in candidates for p in patterns):
            return True
    return False


@contextlib.contextmanager
def _open_output(path: str) -> Iterator[tarfile.TarFile]:
    with open(path, "wb") as f:
        if not path.endswith(".gz"):
            with tarfile.open(fileobj=f, mode="w", format=tarfile.PAX_FORMAT) as tar:
                yield tar
            return
        # No file name and a zero mtime keep the gzip header independent of where and when it ran.
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                yield tar


def write_outputs(export: str, outputs: list[str], exclude: list[str], dest: str) -> None:
    """Write the `outputs` in the runner's `export` tar to `dest`, with the directories leading to
    them, sorted, with zero mtimes and the ownership and modes they had in the image."""
    with tarfile.open(export) as source:
        members = {"/".join(_parts(m.name)): m for m in source.getmembers() if _parts(m.name)}
        kept = {name for name in members if selected(name, outputs) and not excluded(name, exclude)}
        missing = [o for o in outputs if not any(selected(name, [o]) for name in members)]
        if missing:
            raise oci_layout_builder.LayoutError(f"outputs not found in the image: {', '.join(missing)}")
        for name in list(kept):
            parts = name.split("/")
            kept.update("/".join(parts[:i]) for i in range(1, len(parts)))

        if os.path.dirname(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        with _open_output(dest) as out:
            root = tarfile.TarInfo(".")
            root.type, root.mode = tarfile.DIRTYPE, 0o755
            out.addfile(root)
            for name in sorted(kept):
                member = members.get(name)
                if member is None:
                    info = tarfile.TarInfo(name)
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                else:
                    info = copy.copy(member)
                    info.mtime, info.uname, info.gname, info.pax_headers = 0, "", "", {}
                info.name = "./" + name
                data = None
                if info.islnk():
                    target = "/".join(_parts(info.linkname))
                    if target in kept:
                        info.linkname = "./" + target
                    else:
                        # The file it links to isn't an output, so this one carries the content.
                        linked = members[target]
                        info.type, info.linkname, info.size = tarfile.REGTYPE, "", linked.size
                        data = source.extractfile(linked)
                elif info.isreg():
                    data = source.extractfile(member)
                out.addfile(info, data)


def arg_parser(runner: str) -> argparse.ArgumentParser:
    """The command line both builders take; each adds its runner's own options."""
    parser = argparse.ArgumentParser(description=f"Run a scripted build step with {runner}.")
    parser.add_argument("--layout", default="build")
    parser.add_argument("--tag", default="build")
    parser.add_argument("--run", help="JSON argv to run in the image.")
    parser.add_argument("--dest", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    files_parser = commands.add_parser("files", help="Write the image's outputs as a tar to --dest.")
    files_parser.add_argument("--output", action="append", required=True)
    files_parser.add_argument("--exclude", action="append", default=[])
    commands.add_parser("layer", help="Write the layer --run adds to --dest.")
    return parser


def parse_args(
    parser: argparse.ArgumentParser, argv: list[str]
) -> tuple[argparse.Namespace, list[str] | None]:
    """The parsed arguments and the command to run, if any."""
    args = parser.parse_args(argv)
    run = json.loads(args.run) if args.run else None
    if args.command == "layer" and not run:
        parser.error("layer needs --run")
    return args, run
