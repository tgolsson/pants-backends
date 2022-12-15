from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.core.goals.package import PackageFieldSet
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import SkopeoTool
from pants_backend_oci.target_types import ImageDigest, ImageRepository, ImageRepositoryAnonymous
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
)


@dataclass(frozen=True)
class ImageBundlePullFieldSet(FieldSet):
    required_fields = (ImageDigest, ImageRepository)

    repository: ImageRepository
    digest: ImageDigest
    anonymous: ImageRepositoryAnonymous


class ImageBundlePullRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageBundlePullFieldSet


@dataclass(frozen=True)
class PullImageFieldset(PackageFieldSet):
    required_fields = (ImageRepository, ImageDigest)

    repository: ImageRepository
    digest: ImageDigest


@rule(level=LogLevel.DEBUG)
async def pull_oci_image(
    request: ImageBundlePullRequest, skopeo_tool: SkopeoTool, platform: Platform
) -> FallibleImageBundle:
    skopeo = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        skopeo_tool.get_request(platform),
    )

    args = [
        skopeo.exe,
        # TODO[TSOL]: Should likely provide a way to inject a policy
        # into this... Maybe dependency injector?
        "--insecure-policy",
        "copy",
    ]

    if request.target.anonymous:
        args.append("--src-no-creds")

    args.extend(
        [
            f"docker://{request.target.repository.value}@sha256:{request.target.digest.value}",
            "oci:build:build",
        ]
    )

    desc = f"Download OCI image {request.target.repository.value}@sha256:{request.target.digest.value}"

    result = await Get(
        ProcessResult,
        Process(
            argv=tuple(args),
            input_digest=skopeo.digest,
            description=desc,
            output_directories=("build",),
        ),
    )

    return FallibleImageBundle(
        ImageBundle(result.output_digest, f"sha256:{request.target.digest.value}", is_local=False)
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, ImageBundlePullRequest),
    ]
