from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.internals.graph import resolve_target
from pants.engine.process import fallible_to_exec_result_or_raise
from pants.engine.rules import Get, UnionRule, collect_rules, implicitly, rule
from pants.engine.target import FieldSet, Target, WrappedTargetRequest

from pants_backend_kustomize.requests import KustomizeInjectData, KustomizeInjectRequest
from pants_backend_oci.goals.publish import OciPublishProcessRequest, publish_oci_process
from pants_backend_oci.target_types import ImageDigest, ImageRepository, ImageTag
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
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
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.target.address, description_of_origin="package_oci_image"),
        **implicitly(),
    )
    image_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())
    image = await Get(FallibleImageBundle, FallibleImageBundleRequest, image_request.request)
    if image.exit_code != 0 or image.dependency_failed:
        raise Exception(
            f"Failed packaging image:\n{image.stderr}",
        )

    image_digest = image.output.digest
    field_set = request.target
    if image.output.is_local:
        process = await publish_oci_process(
            OciPublishProcessRequest(
                input_digest=image_digest,
                repository=field_set.repository.value,
                tag=field_set.tag.value,
                description=(
                    f"Publish OCI Image {field_set.address} ->"
                    f" {field_set.repository.value}:{field_set.tag.value}"
                ),
            ),
            **implicitly(),
        )

        await fallible_to_exec_result_or_raise(**implicitly(process))

    return KustomizeInjectData(request.target.address, image.output.image_sha)


def rules():
    return [
        *collect_rules(),
        UnionRule(KustomizeInjectRequest, KustomizeInjectOciTagRequest),
    ]
