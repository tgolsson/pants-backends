"""

"""


from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    DictStringToStringField,
    GeneratedTargets,
    GenerateTargetsRequest,
    Target,
    TargetGenerator,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.strutil import softwrap

from pants_backend_oci.target_types import (
    ImageBase,
    ImageDependencies,
    ImageDigest,
    ImageRepository,
    ImageTag,
)


# Only used here
class _ImageVariants(DictStringToStringField):
    alias = "variants"
    help = softwrap(
        """
        The digests to use and names to export as.
        """
    )


class PullImage(Target):
    alias = "oci_pull_image"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        ImageDigest,
    )
    help = "An imported OCI image."


class PullImagesGenerator(TargetGenerator):
    alias = "oci_pull_images"
    help = softwrap(
        """
        The list of base images to import.
        """
    )
    generated_target_cls = PullImage
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        _ImageVariants,
    )
    copied_fields = (*COMMON_TARGET_FIELDS,)
    moved_fields = tuple()


class GenerateFromPullImagesRequest(GenerateTargetsRequest):
    generate_from = PullImagesGenerator


@rule
async def generate_from_oci_base_images(
    request: GenerateFromPullImagesRequest,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    generator = request.generator

    def create_tgt(name: str, digest: str) -> PullImage:

        return PullImage(
            {
                **request.template,
                ImageDigest.alias: digest,
                ImageRepository.alias: generator[ImageRepository].value,
            },
            request.template_address.create_generated(name),
            union_membership,
        )

    result = [
        create_tgt(name, digest) for (name, digest) in generator[_ImageVariants].value.items()
    ]

    return GeneratedTargets(generator, result)


class ImageBuild(Target):
    alias = "oci_image_build"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        ImageTag,
        ImageBase,
        ImageDependencies,
    )
    help = "An imported OCI image."


def targets():
    return [PullImage, PullImagesGenerator, ImageBuild]


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromPullImagesRequest),
    ]
