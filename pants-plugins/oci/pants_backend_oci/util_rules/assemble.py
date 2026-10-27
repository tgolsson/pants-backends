# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Assembling OCI images: the rules describe the image as a list of ops, and
`oci_layout_builder` applies them to the layout in a sandboxed process."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Union

from pants.engine.addresses import Address
from pants.engine.fs import AddPrefix, CreateDigest, Digest, FileContent, MergeDigests
from pants.engine.intrinsics import add_prefix, create_digest, execute_process, merge_digests
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciSubsystem
from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.layer import LAYER_GZIP_THREADS, ImageLayer, gzip_args
from pants_backend_oci.util_rules.oci_layout_builder import (
    AUTHOR,
    BUILT_BY,
    EPOCH,
    LAYER_DESCRIPTOR,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder

_SPEC = "__oci_layout_spec.json"
_COMPRESSED = "compressed"


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
    """Add `layer`, compressed with its descriptor at `path` in the sandbox, and apply the config
    it brings."""
    target = f"Layer target: {layer.address}"
    created_by = layer.created_by or target
    history = {"created": EPOCH, "created_by": created_by, "author": AUTHOR, "comment": target}
    config = ConfigEdit(
        env=(BUILT_BY,),
        entrypoint=(layer.entrypoint,) if layer.entrypoint else None,
        author=AUTHOR,
        created=EPOCH,
    )
    return [{"op": "add_blob", "path": path, "history": history}, config.to_json()]


@dataclass(frozen=True)
class CompressLayerRequest:
    layer: ImageLayer


@dataclass(frozen=True)
class CompressedLayer:
    """The gzipped layer blob and its descriptor at `descriptor`."""

    digest: Digest
    descriptor: str


@rule(desc="Compress OCI layer", level=LogLevel.DEBUG)
async def compress_image_layer(
    request: CompressLayerRequest, builder: OciBuilder, oci: OciSubsystem
) -> CompressedLayer:
    if request.layer.descriptor:
        return CompressedLayer(request.layer.digest, request.layer.descriptor)
    # Only the layer and the builder are inputs, so an unchanged layer is compressed once and its
    # blob reused by every image that adds it.
    process = builder.process(
        oci_layout_builder,
        "compress",
        request.layer.path,
        "--out",
        _COMPRESSED,
        *gzip_args(oci),
        input_digest=request.layer.digest,
        output_directories=(_COMPRESSED,),
        concurrency_available=LAYER_GZIP_THREADS,
        description=f"Compress OCI layer: {request.layer.address}",
        level=LogLevel.DEBUG,
    )
    result = await fallible_to_exec_result_or_raise(**implicitly({process: Process}))
    return CompressedLayer(result.output_digest, f"{_COMPRESSED}/{LAYER_DESCRIPTOR}")


@dataclass(frozen=True)
class AssembleImageRequest:
    """Apply `ops` to the layout in `base` (under `build/`), or to a new image after `InitImage`."""

    base: Digest
    ops: tuple[AssembleOp, ...]
    description: str


@rule(desc="Assemble OCI image", level=LogLevel.DEBUG)
async def assemble_image(request: AssembleImageRequest, builder: OciBuilder) -> FallibleImageBundle:
    layers = [op for op in request.ops if isinstance(op, ImageLayer)]
    compressed = await concurrently(
        compress_image_layer(CompressLayerRequest(layer), **implicitly()) for layer in layers
    )

    spec: list[dict] = []
    prefixed = []
    for op in request.ops:
        if isinstance(op, ImageLayer):
            # Layers are prefixed by position, since different layers may hold the same blob.
            prefix = f"layers/{len(prefixed)}"
            layer = compressed[len(prefixed)]
            prefixed.append(add_prefix(AddPrefix(layer.digest, prefix)))
            spec.extend(layer_ops(op, f"{prefix}/{layer.descriptor}"))
        else:
            spec.append(op.to_json())

    spec_digest, *layer_digests = await concurrently(
        create_digest(CreateDigest([FileContent(_SPEC, json.dumps(spec).encode())])),
        *prefixed,
    )
    input_digest = await merge_digests(MergeDigests([request.base, spec_digest, *layer_digests]))

    result = await execute_process(
        builder.process(
            oci_layout_builder,
            "apply",
            _SPEC,
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
