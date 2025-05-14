from pants.core.goals.package import OutputPathField
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
    ImageArchitectureField,
    ImageArgs,
    ImageArtifactExclusions,
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
    ImageDigest,
    ImageEmptyMarker,
    ImageEntrypoint,
    ImageEnvironment,
    ImageExtractMarker,
    ImageLayerOutputPathField,
    ImageLayersField,
    ImageOsField,
    ImageRepository,
    ImageRepositoryAnonymous,
    ImageRunTty,
    ImageTag,
)


# Only used here
class _ImageVariants(DictStringToStringField):
    alias = "variants"
    help = softwrap("""
        The digests to use and names to export as.
        """)


class PullImage(Target):
    alias = "oci_pull_image"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        ImageRepositoryAnonymous,
        ImageDigest,
        ImageOsField,
        ImageArchitectureField,
    )
    help = "An imported OCI image."


class PullImagesGenerator(TargetGenerator):
    alias = "oci_pull_images"
    help = softwrap("""
        The list of base images to import.
        """)
    generated_target_cls = PullImage
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        ImageRepositoryAnonymous,
        ImageOsField,
        ImageArchitectureField,
        _ImageVariants,
    )
    copied_fields = (
        *COMMON_TARGET_FIELDS,
        ImageOsField,
        ImageArchitectureField,
    )
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
                ImageRepositoryAnonymous.alias: generator[ImageRepositoryAnonymous].value,
            },
            request.template_address.create_generated(name),
            union_membership,
        )

    result = [create_tgt(name, digest) for (name, digest) in generator[_ImageVariants].value.items()]

    return GeneratedTargets(generator, result)


class ImageBuild(Target):
    alias = "oci_image_build"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageRepository,
        ImageTag,
        ImageBase,
        ImageLayersField,
        ImageDependencies,
        ImageRunTty,
        ImageEnvironment,
        ImageEntrypoint,
        ImageArgs,
        OutputPathField,
        ImageBuildCommand,
    )
    help = "An imported OCI image."


class ImageEmpty(Target):
    alias = "oci_image_empty"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageEmptyMarker,
    )
    help = "An imported OCI image."


class ImageBuildStep(Target):
    alias = "oci_build_layer"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageBase,
        ImageDependencies,
        ImageBuildOutputs,
        ImageBuildCommand,
        ImageEnvironment,
        ImageArtifactExclusions,
        OutputPathField,
    )
    help = "An imported OCI image."


class ImageExtractStep(Target):
    alias = "oci_extract"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageBase,
        ImageBuildOutputs,
        ImageEnvironment,
        ImageExtractMarker,
        ImageArtifactExclusions,
        OutputPathField,
    )
    help = "Extracts artifacts from an image."


class ImageLayer(Target):
    alias = "oci_layer"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ImageDependencies,
        ImageLayerOutputPathField,
    )
    help = "A layer that can be inserted directly into an imgae"


def targets():
    return [
        PullImage,
        PullImagesGenerator,
        ImageBuild,
        ImageBuildStep,
        ImageEmpty,
        ImageLayer,
        ImageExtractStep,
    ]


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromPullImagesRequest),
    ]
