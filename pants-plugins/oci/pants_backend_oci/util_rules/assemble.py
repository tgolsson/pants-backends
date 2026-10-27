# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Assembling OCI images: the rules describe the image as a list of ops, and
`oci_layout_builder` applies them to the layout in a sandboxed process."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from typing import Union

from pants.engine.addresses import Address
from pants.engine.fs import (
    EMPTY_DIGEST,
    AddPrefix,
    CreateDigest,
    Digest,
    DigestSubset,
    FileContent,
    GlobExpansionConjunction,
    GlobMatchErrorBehavior,
    MergeDigests,
    PathGlobs,
    RemovePrefix,
)
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_subset_to_digest,
    execute_process,
    merge_digests,
    remove_prefix,
)
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
    """Apply `ops` to the image in `base`, or to a new image after `InitImage`."""

    base: ImageBundle | None
    ops: tuple[AssembleOp, ...]
    description: str


@rule(desc="Assemble OCI image", level=LogLevel.DEBUG)
async def assemble_image(request: AssembleImageRequest, builder: OciBuilder) -> FallibleImageBundle:
    layers = [op for op in request.ops if isinstance(op, ImageLayer)]
    compressed = await concurrently(
        compress_image_layer(CompressLayerRequest(layer), **implicitly()) for layer in layers
    )

    spec: list[dict] = []
    added = 0
    for op in request.ops:
        if isinstance(op, ImageLayer):
            # Layers are prefixed by position, since different layers may hold the same blob.
            spec.extend(layer_ops(op, f"layers/{added}/{compressed[added].descriptor}"))
            added += 1
        else:
            spec.append(op.to_json())

    # The process gets metadata only: the base's index, manifest and config, and each layer's
    # descriptor. The blobs are merged into the layout afterwards, so assembly never copies them.
    descriptors = await concurrently(
        digest_subset_to_digest(DigestSubset(layer.digest, PathGlobs([layer.descriptor])))
        for layer in compressed
    )
    spec_digest, *prefixed = await concurrently(
        create_digest(CreateDigest([FileContent(_SPEC, json.dumps(spec).encode())])),
        *(add_prefix(AddPrefix(digest, f"layers/{i}")) for i, digest in enumerate(descriptors)),
    )
    base_metadata = request.base.metadata if request.base else EMPTY_DIGEST
    input_digest = await merge_digests(MergeDigests([base_metadata, spec_digest, *prefixed]))

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

    image = json.loads(result.stdout)
    # The captured layout is the new metadata. The full layout adds the base's and the layers'
    # blobs, keeping those the image references.
    base_blobs, *layer_blobs = await concurrently(
        digest_subset_to_digest(
            DigestSubset(request.base.digest if request.base else EMPTY_DIGEST, PathGlobs(["build/blobs/**"]))
        ),
        *(
            digest_subset_to_digest(DigestSubset(layer.digest, PathGlobs([f"{_blob_dir(layer)}/blobs/**"])))
            for layer in compressed
        ),
    )
    layer_blobs = await concurrently(
        remove_prefix(RemovePrefix(blobs, _blob_dir(layer))) for blobs, layer in zip(layer_blobs, compressed)
    )
    layer_layout = await add_prefix(AddPrefix(await merge_digests(MergeDigests(layer_blobs)), "build"))
    layout = await merge_digests(MergeDigests([result.output_digest, base_blobs, layer_layout]))
    if image["blobs"] is not None:
        kept = [f"build/blobs/{digest.replace(':', '/', 1)}" for digest in image["blobs"]]
        layout = await digest_subset_to_digest(
            DigestSubset(
                layout,
                PathGlobs(
                    ["build/index.json", "build/oci-layout", *kept],
                    glob_match_error_behavior=GlobMatchErrorBehavior.error,
                    conjunction=GlobExpansionConjunction.all_match,
                    description_of_origin=request.description,
                ),
            )
        )
    return FallibleImageBundle(
        ImageBundle(layout, image_sha=image["digest"], is_local=True, metadata=result.output_digest)
    )


def _blob_dir(layer: CompressedLayer) -> str:
    """The directory holding the layer's `blobs/`, beside its descriptor."""
    directory = posixpath.dirname(layer.descriptor)
    if not directory:
        raise ValueError(f"Expected the layer descriptor {layer.descriptor!r} in a directory")
    return directory


class ImageAssemblyError(Exception):
    pass


def assembled_or_raise(image: FallibleImageBundle, address: Address) -> ImageBundle:
    if image.output is None:
        raise ImageAssemblyError(f"Failed to assemble the OCI image for {address}:\n{image.stderr}")
    return image.output


def rules():
    return collect_rules()
