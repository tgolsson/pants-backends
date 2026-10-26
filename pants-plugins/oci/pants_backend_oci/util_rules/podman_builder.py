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

import json
import os
import pwd
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from pants_backend_oci.util_rules import container_build, oci_layout_builder, podman_runner

_ARCHIVE = "image.tar"
_CONTEXT = "podman-context"


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


def main(argv: list[str]) -> int:
    parser = container_build.arg_parser("podman")
    parser.add_argument("--podman", required=True)
    parser.add_argument("--cache", required=True, type=Path)
    args, run = container_build.parse_args(parser, argv)

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
    Path(_CONTEXT, "Containerfile").write_text(
        container_build.containerfile(f"oci-archive:{archive}:{ref}", run, outputs)
    )

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
                container_build.write_outputs(export, outputs, args.exclude, args.dest)
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
