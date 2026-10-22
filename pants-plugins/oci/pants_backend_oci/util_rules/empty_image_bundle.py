""" """

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.fs import EMPTY_DIGEST
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionRule

from pants_backend_oci.target_types import ImageEmptyMarker
from pants_backend_oci.util_rules.assemble import AssembleImageRequest, InitImage, assemble_image
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
)


@dataclass(frozen=True)
class ImageBundleEmptyFieldSet(FieldSet):
    required_fields = (ImageEmptyMarker,)


class ImageBundleEmptyRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageBundleEmptyFieldSet


@rule
async def make_empty_oci_image(request: ImageBundleEmptyRequest, platform: Platform) -> FallibleImageBundle:
    # The empty image is for the host's architecture.
    architecture = "arm64" if platform.value.endswith("arm64") else "amd64"
    return await assemble_image(
        AssembleImageRequest(
            EMPTY_DIGEST, (InitImage(architecture, "linux"),), "Creating an empty OCI image"
        ),
        **implicitly(),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, ImageBundleEmptyRequest),
    ]
