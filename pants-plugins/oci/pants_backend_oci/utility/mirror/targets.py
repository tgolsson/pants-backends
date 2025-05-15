""""""

from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    GeneratedTargets,
    GenerateTargetsRequest,
    StringField,
    Target,
    TargetGenerator,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.strutil import softwrap

from pants_backend_oci.target_types import ImageDigest, ImageTag
from pants_backend_oci.targets import _ImageVariants


class ImageRepository(StringField):
    alias = "repository"
    help = softwrap(
        """
        The repository to import the image from.
        """
    )


class SourceRepository(ImageRepository):
    alias = "source_repository"
    help = "Where to download images from"


class DestinationRepository(ImageRepository):
    alias = "destination_repository"
    help = "Where to push images to"


class MirrorImage(Target):
    alias = "oci_mirror_image"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        SourceRepository,
        DestinationRepository,
        ImageDigest,
        ImageTag,
    )
    help = "An imported OCI image."


class MirrorImagesGenerator(TargetGenerator):
    alias = "oci_mirror_images"
    help = softwrap(
        """
        The list of base images to import.
        """
    )
    generated_target_cls = MirrorImage
    core_fields = (
        *COMMON_TARGET_FIELDS,
        SourceRepository,
        DestinationRepository,
        _ImageVariants,
    )
    copied_fields = (
        *COMMON_TARGET_FIELDS,
        SourceRepository,
        DestinationRepository,
    )
    moved_fields = tuple()


class GenerateFromMirrorImagesRequest(GenerateTargetsRequest):
    generate_from = MirrorImagesGenerator


@rule
async def generate_from_oci_base_images(
    request: GenerateFromMirrorImagesRequest,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    generator = request.generator

    def create_tgt(name: str, digest: str) -> MirrorImage:
        return MirrorImage(
            {
                **request.template,
                ImageDigest.alias: digest,
                ImageTag.alias: name,
            },
            request.template_address.create_generated(name),
            union_membership,
        )

    result = [create_tgt(name, digest) for (name, digest) in generator[_ImageVariants].value.items()]

    return GeneratedTargets(generator, result)


def targets():
    return [MirrorImage, MirrorImagesGenerator]


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromMirrorImagesRequest),
    ]
