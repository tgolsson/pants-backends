# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Runs scripted build steps with `podman build` on an OCI layout.

This module runs inside a sandboxed process, with the rules only deciding what to do, so it uses
only the standard library and never imports Pants.

The layout is handed to podman as an OCI archive. `files` runs the command, if there is one, and
writes the image's `outputs` as a deterministic tar. `layer` runs the command and writes the layer
it produced, for the rules to append to the original layout so the base's layers are kept as they
are. Scripted output is only as reproducible as the command: `--timestamp 0` and the tar's fixed
mtimes cover the times podman records, but nothing else.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fnmatch
import gzip
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Iterator

from pants_backend_oci.util_rules import oci_layout_builder, podman_runner

_ARCHIVE = "image.tar"
_CONTEXT = "podman-context"
_GLOB = re.compile(r"[*?[]")


def _parts(path: str) -> list[str]:
    return [part for part in path.removeprefix("./").strip("/").split("/") if part]


def copy_root(output: str) -> str:
    """The directory to copy out of the image for `output`: itself, or the part of a glob before
    its first wildcard, so the matches keep their paths and are picked out afterwards."""
    parts = _parts(output)
    static = next((i for i, part in enumerate(parts) if _GLOB.search(part)), len(parts))
    return "/" + "/".join(parts[:static])


def containerfile(archive: str, ref: str, run: list[str] | None, outputs: list[str]) -> str:
    """The build stage runs `run` as root; `outputs` add a scratch stage holding only what they
    need."""
    lines = [f"FROM oci-archive:{archive}:{ref} AS build"]
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


def build_argv(podman: str, storage: list[str], context: str, iidfile: str, dest: str | None) -> list[str]:
    argv = [
        podman,
        *storage,
        "build",
        "--quiet",
        "--layers=false",
        # Podman otherwise leaves its own /etc/hosts in the image.
        "--no-hosts",
        "--timestamp=0",
        f"--iidfile={iidfile}",
    ]
    if dest is not None:
        # A tar keeps ownership and hardlinks, which a directory export loses.
        argv.append(f"--output=type=tar,dest={dest}")
    argv.extend(["--file", os.path.join(context, "Containerfile"), context])
    return argv


def write_archive(layout: str, tag: str, archive: str) -> str:
    """Write the image tagged `tag` as an OCI archive, and return the name it's tagged with there.

    The name is the manifest digest, so builds running at once never import different images
    under the same name into the shared store.
    """
    digest = oci_layout_builder.image_digest(layout, tag)
    ref = "pants-" + digest.partition(":")[2]
    oci_layout_builder.write_archive(layout, tag, archive, ref)
    return ref


def top_layer(saved: str, base_layers: int) -> str:
    """The path of the one layer `podman save --format oci-dir` added on top of the base."""
    index = oci_layout_builder.read_index(saved)
    if len(index.get("manifests", [])) != 1:
        raise oci_layout_builder.LayoutError("expected podman to save one image")
    manifest = json.loads(oci_layout_builder.read_blob(saved, index["manifests"][0]["digest"]))
    layers = manifest.get("layers", [])
    if len(layers) != base_layers + 1:
        raise oci_layout_builder.LayoutError(
            f"expected the build step to add one layer to the base's {base_layers}, found {len(layers)}"
        )
    return oci_layout_builder.blob_path(saved, layers[-1]["digest"])


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
    """Write the `outputs` in podman's `export` tar to `dest`, with the directories leading to
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run a scripted build step with podman.")
    parser.add_argument("--podman", required=True)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--layout", default="build")
    parser.add_argument("--tag", default="build")
    parser.add_argument("--run", help="JSON argv to run in the image.")
    parser.add_argument("--dest", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    files_parser = commands.add_parser("files", help="Write the image's outputs as a tar to --dest.")
    files_parser.add_argument("--output", action="append", required=True)
    files_parser.add_argument("--exclude", action="append", default=[])
    commands.add_parser("layer", help="Write the layer --run adds to --dest.")
    args = parser.parse_args(argv)
    run = json.loads(args.run) if args.run else None
    if args.command == "layer" and not run:
        parser.error("layer needs --run")

    try:
        podman_runner.check_local("Scripted OCI build steps")
        cache, runtime, env = podman_runner.prepare_store(args.cache)
    except podman_runner.PodmanUnavailable as e:
        print(e, file=sys.stderr)
        return 1
    # Builders run without a HOME. Podman reads its per-user configuration from the user's home, as
    # it does for `pants run`; looking it up here keeps it out of the cache key.
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    storage = podman_runner.storage_args(cache, runtime)

    archive = os.path.abspath(_ARCHIVE)
    try:
        ref = write_archive(args.layout, args.tag, archive)
    except (KeyError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    os.makedirs(_CONTEXT, exist_ok=True)
    outputs = args.output if args.command == "files" else []
    Path(_CONTEXT, "Containerfile").write_text(containerfile(archive, ref, run, outputs))

    with tempfile.TemporaryDirectory(dir=".") as tmp:
        iidfile = os.path.join(tmp, "iid")
        export = os.path.abspath(os.path.join(tmp, "export.tar")) if args.command == "files" else None
        try:
            try:
                result = subprocess.run(build_argv(args.podman, storage, _CONTEXT, iidfile, export), env=env)
            except FileNotFoundError:
                print("podman is missing. Install it: https://podman.io/docs/installation", file=sys.stderr)
                return 127
            if result.returncode != 0:
                return result.returncode
            if export is not None:
                write_outputs(export, outputs, args.exclude, args.dest)
                return 0

            image = Path(iidfile).read_text().strip()
            saved = os.path.join(tmp, "saved")
            save = [args.podman, *storage, "save", "--quiet", "--format=oci-dir", f"--output={saved}", image]
            result = subprocess.run(save, env=env)
            if result.returncode != 0:
                return result.returncode
            base_layers = len(oci_layout_builder.load_image(args.layout, args.tag).manifest["layers"])
            shutil.copyfile(top_layer(saved, base_layers), args.dest)
        except (KeyError, ValueError, OSError, tarfile.TarError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        finally:
            # The imported base stays in the store for the next build; the built image doesn't.
            if os.path.exists(iidfile):
                image = Path(iidfile).read_text().strip()
                subprocess.run([args.podman, *storage, "rmi", "--force", image], env=env, capture_output=True)
    return 0
