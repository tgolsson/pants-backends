""" """

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import ClassVar

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionRule

from pants_backend_oci.subsystem import UmociTool
from pants_backend_oci.target_types import ImageEmptyMarker
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
)
from pants_backend_oci.util_rules.oci_sha import OciSha, OciShaRequest


@dataclass(frozen=True)
class ImageBundleEmptyFieldSet(FieldSet):
    required_fields = (ImageEmptyMarker,)


class ImageBundleEmptyRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageBundleEmptyFieldSet


@rule
async def make_empty_oci_image(
    request: ImageBundleEmptyRequest,
    platform: Platform,
    umoci: UmociTool,
) -> FallibleImageBundle:
    umoci = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        umoci.get_request(platform),
    )

    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"
    result = await Get(
        ProcessResult,
        FusedProcess(
            (
                Process(
                    argv=(umoci.exe, "init", "--layout", "build"),
                    input_digest=umoci.digest,
                    description="Creating base OCI layout",
                ),
                Process(
                    argv=(umoci.exe, "new", "--image", "build:build"),
                    input_digest=umoci.digest,
                    description="Creating a new empty base image",
                    output_directories=("build",),
                ),
                Process(
                    argv=(
                        umoci.exe,
                        "config",
                        "--image",
                        "build:build",
                        "--config.env",
                        "BUILT_BY=pants.oci",
                        "--author=pants_backend_oci",
                        f"--created={timestamp}",
                        "--no-history",
                    ),
                    description="Erasing timestamps and other info",
                ),
            ),
        ),
    )

    image_digest = await Get(OciSha, OciShaRequest(result.output_digest))
    return FallibleImageBundle(
        ImageBundle(result.output_digest, image_sha=image_digest.image_digest, is_local=True)
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, ImageBundleEmptyRequest),
    ]
