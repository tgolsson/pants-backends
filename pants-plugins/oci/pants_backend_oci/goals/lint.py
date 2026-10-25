# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Linting images for `.deb` layers that overwrite files from the layers below them."""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from typing import Any

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import (
    AddPrefix,
    CreateDigest,
    Digest,
    DigestSubset,
    FileContent,
    MergeDigests,
    PathGlobs,
)
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_subset_to_digest,
    execute_process,
    merge_digests,
)
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import DebLayerOverwrites
from pants_backend_oci.target_types import DebLayerPackagesField
from pants_backend_oci.util_rules import layer_index
from pants_backend_oci.util_rules.build_image_bundle import (
    BuildImageBundleFieldSet,
    ImageLayersRequest,
    image_layer_requests,
)
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundleRequest,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.layer import build_image_layer
from pants_backend_oci.util_rules.sandboxed import OciBuilder

_INDEX = "__layer_index.json"
_SPEC = "__overwrites_spec.json"


class DebLayerOverwritesRequest(LintTargetsRequest):
    field_set_type = BuildImageBundleFieldSet
    tool_subsystem = DebLayerOverwrites
    partitioner_type = PartitionerType.DEFAULT_ONE_PARTITION_PER_INPUT


@dataclass(frozen=True)
class LayerIndexRequest:
    """Index the layer tar at `path` in `inputs` with `layer_index`."""

    inputs: Digest
    path: str
    description: str


@dataclass(frozen=True)
class LayerIndex:
    """The index under `_INDEX`, or why it couldn't be made."""

    digest: Digest | None
    stderr: str = ""


@rule(desc="Index OCI layer", level=LogLevel.DEBUG)
async def index_layer(request: LayerIndexRequest, builder: OciBuilder) -> LayerIndex:
    # Only the builder and the layer are inputs, so each layer is indexed once.
    result = await execute_process(
        builder.process(
            layer_index,
            "index-layer",
            request.path,
            "--out",
            _INDEX,
            input_digest=request.inputs,
            output_files=(_INDEX,),
            description=request.description,
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        return LayerIndex(None, f"{request.description} failed:\n{result.stderr.decode(errors='replace')}")
    return LayerIndex(result.output_digest)


@dataclass(frozen=True)
class BaseLayersRequest:
    """The layer blobs of the image layout in `layout`, each alone in a digest."""

    layout: Digest
    address: str


@dataclass(frozen=True)
class BaseLayers:
    blobs: tuple[tuple[Digest, str], ...]
    stderr: str = ""


@rule(desc="List OCI image layers", level=LogLevel.DEBUG)
async def base_layers(request: BaseLayersRequest, builder: OciBuilder) -> BaseLayers:
    result = await execute_process(
        builder.process(
            layer_index,
            "layer-blobs",
            "build",
            input_digest=request.layout,
            description=f"List the layers of OCI image: {request.address}",
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if result.exit_code != 0:
        stderr = result.stderr.decode(errors="replace")
        return BaseLayers((), f"Listing the layers of {request.address} failed:\n{stderr}")
    paths = json.loads(result.stdout)
    # Each blob is indexed on its own, so a layer is read once whichever images it's in.
    digests = await concurrently(
        digest_subset_to_digest(DigestSubset(request.layout, PathGlobs([path]))) for path in paths
    )
    return BaseLayers(tuple(zip(digests, paths)))


@rule(desc="Check .deb layers for overwritten files", level=LogLevel.DEBUG)
async def lint_deb_layer_overwrites(
    request: DebLayerOverwritesRequest.Batch[BuildImageBundleFieldSet, Any],
    builder: OciBuilder,
) -> LintResult:
    (field_set,) = request.elements
    requests = await image_layer_requests(ImageLayersRequest(field_set), **implicitly())

    def failed(stdout: str, stderr: str) -> LintResult:
        return LintResult(1, stdout, stderr, request.tool_name, request.partition_metadata.description)

    # Only the layers up to the last `.deb` layer can be overwritten by one.
    checked = [r.target.has_field(DebLayerPackagesField) for r in requests.layers]
    if not any(checked):
        return LintResult(0, "", "", request.tool_name, request.partition_metadata.description)
    checked = checked[: len(checked) - checked[::-1].index(True)]

    base_address = str(requests.base.target.address)
    base, *layers = await concurrently(
        resolve_image_bundle(**implicitly({requests.base: FallibleImageBundleRequest})),
        *(build_image_layer(r) for r in requests.layers[: len(checked)]),
    )
    if base.output is None:
        return failed(
            base.stdout or "", base.stderr or f"Failed to build the base image of {field_set.address}"
        )

    blobs = await base_layers(BaseLayersRequest(base.output.digest, base_address), **implicitly())
    if blobs.stderr:
        return failed("", blobs.stderr)

    indexes = await concurrently(
        *(
            index_layer(
                LayerIndexRequest(digest, path, f"Index OCI layer blob {posixpath.basename(path)}"),
                **implicitly(),
            )
            for digest, path in blobs.blobs
        ),
        *(
            index_layer(
                LayerIndexRequest(layer.digest, layer.path, f"Index OCI layer: {layer.address}"),
                **implicitly(),
            )
            for layer in layers
        ),
    )
    errors = [index.stderr for index in indexes if index.digest is None]
    if errors:
        return failed("", "\n".join(errors))

    base_labels = (
        [base_address]
        if len(blobs.blobs) == 1
        else [f"{base_address} layer {i + 1}" for i in range(len(blobs.blobs))]
    )
    labels = [*base_labels, *(str(layer.address) for layer in layers)]
    checks = [*(False for _ in base_labels), *checked]
    spec = {
        "layers": [
            {"label": label, "index": f"indexes/{i}/{_INDEX}", "check": check}
            for i, (label, check) in enumerate(zip(labels, checks))
        ]
    }
    spec_digest, *prefixed = await concurrently(
        create_digest(CreateDigest([FileContent(_SPEC, json.dumps(spec).encode())])),
        *(add_prefix(AddPrefix(index.digest, f"indexes/{i}")) for i, index in enumerate(indexes)),
    )
    input_digest = await merge_digests(MergeDigests([spec_digest, *prefixed]))

    result = await execute_process(
        builder.process(
            layer_index,
            "check",
            _SPEC,
            input_digest=input_digest,
            description=f"Check .deb layers for overwritten files: {field_set.address}",
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    return LintResult.create(request, result)


def rules():
    return [
        *collect_rules(),
        *DebLayerOverwritesRequest.rules(),
    ]
