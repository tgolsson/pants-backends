# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Runs scripted build steps with `docker build` on an OCI layout.

This module runs inside a sandboxed process, with the rules only deciding what to do, so it uses
only the standard library and never imports Pants.

The layout is loaded into the Docker daemon from an OCI archive, and built from by name with
BuildKit. `files` and `layer` work as they do with podman (see `podman_builder`). Docker has no
`--timestamp`, so the layer a step adds is rewritten with zero mtimes and no owner names instead:
scripted output is still only as reproducible as the command.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import IO

from pants_backend_oci.util_rules import container_build, oci_layout_builder

_ARCHIVE = "image.tar"
_CONTEXT = "docker-context"
_MISSING = "docker is missing. Install it: https://docs.docker.com/engine/install/"
# Times that would otherwise survive in a layer's PAX headers.
_PAX_TIMES = frozenset({"mtime", "atime", "ctime"})


def image_name(layout: str, tag: str) -> str:
    """The name the image is loaded under: its manifest digest, so builds running at once never
    load different images under the same name. `docker load` needs a tag in the name."""
    return "pants-" + oci_layout_builder.image_digest(layout, tag).partition(":")[2] + ":latest"


def build_argv(docker: str, context: str, tag: str, dest: str | None) -> list[str]:
    """Build into the daemon as `tag`, or, with `dest`, only export the image as a tar there.

    Docker's build cache is off, so the command reruns whenever Pants reruns the step. The tag is
    this build's own: identical builds can produce the same image, and removing it by id would
    remove it from under another build saving it."""
    argv = [docker, "build", "--quiet", "--no-cache"]
    if dest is None:
        argv.append(f"--tag={tag}")
    else:
        # A tar keeps ownership and hardlinks, which a directory export loses.
        argv.append(f"--output=type=tar,dest={dest}")
    argv.extend(["--file", os.path.join(context, "Containerfile"), context])
    return argv


def write_layer(source: IO[bytes], dest: str) -> None:
    """Write the layer tar `source`, compressed or not, to `dest` uncompressed, with zero mtimes and
    no owner names. Entries keep their order, ownership, modes and other PAX headers, such as
    file capabilities."""
    with tarfile.open(fileobj=source, mode="r:*") as layer, open(dest, "wb") as f:
        with tarfile.open(fileobj=f, mode="w", format=tarfile.PAX_FORMAT) as out:
            for member in layer:
                info = copy.copy(member)
                info.mtime, info.uname, info.gname = 0, "", ""
                info.pax_headers = {k: v for k, v in member.pax_headers.items() if k not in _PAX_TIMES}
                out.addfile(info, layer.extractfile(member) if member.isreg() else None)


def write_top_layer(saved: str, base_layers: int, dest: str) -> None:
    """Write the one layer the step added, from the `docker save` tar `saved`, to `dest`.

    The tar's `manifest.json` lists the layers, whether Docker saved them in its own format or,
    from Docker 25, as an OCI layout. A step that changed no files adds no layer with BuildKit, and
    gets an empty one; that relies on `docker save` keeping the base's layers one for one."""
    with tarfile.open(saved) as tar:
        manifest_file = tar.extractfile("manifest.json")
        if manifest_file is None:
            raise oci_layout_builder.LayoutError("docker save wrote no manifest.json")
        manifest = json.load(manifest_file)
        if len(manifest) != 1:
            raise oci_layout_builder.LayoutError("expected docker to save one image")
        layers = manifest[0]["Layers"]
        if len(layers) == base_layers:
            tarfile.open(dest, "w", format=tarfile.PAX_FORMAT).close()
            return
        if len(layers) != base_layers + 1:
            raise oci_layout_builder.LayoutError(
                f"expected the build step to add one layer to the base's {base_layers}, found {len(layers)}"
            )
        layer = tar.extractfile(layers[-1])
        if layer is None:
            raise oci_layout_builder.LayoutError(f"docker save has no layer {layers[-1]}")
        write_layer(layer, dest)


def _docker(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    """Run docker quietly, printing what it wrote only when it fails."""
    result = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        sys.stderr.write(result.stdout.decode(errors="replace"))
    return result


def main(argv: list[str]) -> int:
    parser = container_build.arg_parser("docker")
    parser.add_argument("--docker", required=True)
    args, run = container_build.parse_args(parser, argv)

    env = container_build.host_env()

    archive = os.path.abspath(_ARCHIVE)
    try:
        name = image_name(args.layout, args.tag)
        oci_layout_builder.write_archive(args.layout, args.tag, archive, name)
    except (KeyError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    os.makedirs(_CONTEXT, exist_ok=True)
    outputs = args.output if args.command == "files" else []
    Path(_CONTEXT, "Containerfile").write_text(container_build.containerfile(name, run, outputs))

    built = f"pants-step-{uuid.uuid4().hex}:latest"
    with tempfile.TemporaryDirectory(dir=".") as tmp:
        export = os.path.abspath(os.path.join(tmp, "export.tar")) if args.command == "files" else None
        try:
            try:
                result = _docker([args.docker, "load", "--quiet", "--input", archive], env)
            except FileNotFoundError:
                print(_MISSING, file=sys.stderr)
                return 127
            if result.returncode != 0:
                return result.returncode
            # It prints the image's id, which isn't this step's output; errors go to stderr.
            build = build_argv(args.docker, _CONTEXT, built, export)
            result = subprocess.run(build, env=env, stdout=subprocess.DEVNULL)
            if result.returncode != 0:
                return result.returncode
            if export is not None:
                container_build.write_outputs(export, outputs, args.exclude, args.dest)
                return 0

            saved = os.path.join(tmp, "saved.tar")
            result = _docker([args.docker, "save", "--output", saved, built], env)
            if result.returncode != 0:
                return result.returncode
            base_layers = len(oci_layout_builder.load_image(args.layout, args.tag).manifest["layers"])
            write_top_layer(saved, base_layers, args.dest)
        except (KeyError, ValueError, OSError, tarfile.TarError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        finally:
            # The loaded base stays in the daemon for the next build; the built image doesn't, unless
            # the process is killed first. An export builds no image. Removing the tag removes the
            # image only once nothing else names it.
            if export is None:
                subprocess.run([args.docker, "rmi", built], env=env, capture_output=True)
    return 0
