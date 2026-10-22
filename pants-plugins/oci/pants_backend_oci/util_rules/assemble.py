# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Assembling OCI images: the rules describe the image as a list of ops, and
`oci_layout_builder` applies them to the layout in a sandboxed process."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Union

from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.engine.addresses import Address
from pants.engine.fs import AddPrefix, CreateDigest, Digest, FileContent, MergeDigests
from pants.engine.intrinsics import add_prefix, create_digest, execute_process, merge_digests
from pants.engine.process import Process
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.layer import ImageLayer
from pants_backend_oci.util_rules.oci_layout_builder import AUTHOR, BUILT_BY, EPOCH
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules

_SPEC = "__oci_layout_spec.json"


@dataclass(frozen=True)
class InitImage:
    """Start from an empty image instead of the base layout."""

    architecture: str
    os: str

    def to_json(self) -> dict:
        return {"op": "init", "architecture": self.architecture, "os": self.os}


@dataclass(frozen=True)
class ConfigEdit:
    """Edit the image config. Unset fields are left alone; `history` records the edit."""

    env: tuple[str, ...] = ()
    entrypoint: tuple[str, ...] | None = None
    cmd: tuple[str, ...] | None = None
    user: str | None = None
    clear: tuple[str, ...] = ()
    author: str | None = None
    created: str | None = None
    history: bool = False

    def to_json(self) -> dict:
        op: dict = {"op": "config"}
        if self.clear:
            op["clear"] = list(self.clear)
        if self.env:
            op["env"] = list(self.env)
        for name in ("entrypoint", "cmd"):
            value = getattr(self, name)
            if value is not None:
                op[name] = list(value)
        for name in ("user", "author", "created"):
            value = getattr(self, name)
            if value is not None:
                op[name] = value
        if self.history:
            op["history"] = {"created": EPOCH, "created_by": f"{AUTHOR} config", "author": AUTHOR}
        return op


AssembleOp = Union[InitImage, ConfigEdit, ImageLayer]


def layer_ops(layer: ImageLayer, path: str) -> list[dict]:
    """Add `layer`, found at `path` in the sandbox, and apply the config it brings."""
    created_by = f"Layer target: {layer.address}"
    history = {"created": EPOCH, "created_by": created_by, "author": AUTHOR, "comment": created_by}
    config = ConfigEdit(
        env=(BUILT_BY,),
        entrypoint=(layer.entrypoint,) if layer.entrypoint else None,
        author=AUTHOR,
        created=EPOCH,
    )
    return [{"op": "add_layer", "path": path, "history": history}, config.to_json()]


@dataclass(frozen=True)
class AssembleImageRequest:
    """Apply `ops` to the layout in `base` (under `build/`), or to a new image after `InitImage`."""

    base: Digest
    ops: tuple[AssembleOp, ...]
    description: str


@rule(desc="Assemble OCI image", level=LogLevel.DEBUG)
async def assemble_image(
    request: AssembleImageRequest, python: PythonBuildStandaloneBinary
) -> FallibleImageBundle:
    spec: list[dict] = []
    prefixed = []
    for op in request.ops:
        if isinstance(op, ImageLayer):
            # Layers are prefixed by position, since different layers may share a file name.
            prefix = f"layers/{len(prefixed)}"
            prefixed.append(add_prefix(AddPrefix(op.digest, prefix)))
            spec.extend(layer_ops(op, f"{prefix}/{op.path}"))
        else:
            spec.append(op.to_json())

    builder, spec_digest, *layers = await concurrently(
        create_digest(sandboxed_modules(oci_layout_builder)),
        create_digest(CreateDigest([FileContent(_SPEC, json.dumps(spec).encode())])),
        *prefixed,
    )
    input_digest = await merge_digests(MergeDigests([request.base, builder, spec_digest, *layers]))

    result = await execute_process(
        Process(
            argv=(python.path, "-c", module_main(oci_layout_builder), "apply", _SPEC),
            input_digest=input_digest,
            output_directories=("build",),
            description=request.description,
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        return FallibleImageBundle(
            None,
            result.exit_code,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
        )

    image_sha = result.stdout.decode().strip()
    return FallibleImageBundle(ImageBundle(result.output_digest, image_sha=image_sha, is_local=True))


class ImageAssemblyError(Exception):
    pass


def assembled_or_raise(image: FallibleImageBundle, address: Address) -> ImageBundle:
    if image.output is None:
        raise ImageAssemblyError(f"Failed to assemble the OCI image for {address}:\n{image.stderr}")
    return image.output


def rules():
    return collect_rules()
