from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, UnionRule, collect_rules, rule
from pants.engine.target import FieldSet, Target, WrappedTarget, WrappedTargetRequest

from pants_backend_kustomize.requests import KustomizeInjectData, KustomizeInjectRequest
from pants_backend_oci.goals.publish import OciPublishProcessRequest
from pants_backend_oci.target_types import ImageDigest, ImageRepository, ImageTag
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundleRequest,
)


@dataclass(frozen=True)
class KustomizeInjectOciTagFieldSet(FieldSet):
    required_fields = (ImageRepository,)

    repository: ImageRepository
    tag: ImageTag
    digest: ImageDigest


class KustomizeInjectOciTagRequest(KustomizeInjectRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = KustomizeInjectOciTagFieldSet


@rule(desc="Generating OCI tag")
async def generate_oci_tag_injection(
    request: KustomizeInjectOciTagRequest,
) -> KustomizeInjectData:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.target.address, description_of_origin="package_oci_image"),
    )
    image_request = await Get(FallibleImageBundleRequestWrap, ImageBundleRequest(wrapped_target.target))
    image = await Get(FallibleImageBundle, FallibleImageBundleRequest, image_request.request)
    if image.exit_code != 0 or image.dependency_failed:
        raise Exception(
            f"Failed packaging image:\n{image.stderr}",
        )

    image_digest = image.output.digest
    field_set = request.target
    if image.output.is_local:
        process = await Get(
            Process,
            OciPublishProcessRequest(
                input_digest=image_digest,
                repository=field_set.repository.value,
                tag=field_set.tag.value,
                description=(
                    f"Publish OCI Image {field_set.address} ->"
                    f" {field_set.repository.value}:{field_set.tag.value}"
                ),
            ),
        )

        await Get(ProcessResult, Process, process)

    return KustomizeInjectData(request.target.address, image.output.image_sha)


def rules():
    return [
        *collect_rules(),
        UnionRule(KustomizeInjectRequest, KustomizeInjectOciTagRequest),
    ]
