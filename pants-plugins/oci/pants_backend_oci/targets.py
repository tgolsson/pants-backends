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
    CaCertsPackagesField,
    DebLayerMergedUsrField,
    DebLayerPackagesField,
    GroupEntriesField,
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
    ImageUser,
    PasswdUsersField,
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
        ImageUser,
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


class DebLayer(Target):
    alias = "oci_deb_layer"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        DebLayerPackagesField,
        DebLayerMergedUsrField,
        OutputPathField,
    )
    help = softwrap("""
        A layer built from `.deb` packages without running dpkg, apt or maintainer scripts.

        Package contents are extracted as-is and each package is recorded in
        `/var/lib/dpkg/status.d/`, like distroless images, so image scanners can see it.
        """)


class PasswdLayer(Target):
    alias = "oci_passwd_layer"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        PasswdUsersField,
        OutputPathField,
    )
    help = softwrap("""
        A layer with `/etc/passwd` and the users' home directories, replacing `useradd` in a
        scripted build.
        """)


class GroupLayer(Target):
    alias = "oci_group_layer"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        GroupEntriesField,
        OutputPathField,
    )
    help = softwrap("""
        A layer with `/etc/group`, replacing `groupadd` in a scripted build.
        """)


class CaCertsLayer(Target):
    alias = "oci_cacerts_layer"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CaCertsPackagesField,
        OutputPathField,
    )
    help = softwrap("""
        A layer with the CA bundle `/etc/ssl/certs/ca-certificates.crt`, generated from the
        `ca-certificates` package without running `update-ca-certificates`.
        """)


def targets():
    return [
        PullImage,
        PullImagesGenerator,
        ImageBuild,
        ImageBuildStep,
        ImageEmpty,
        ImageLayer,
        ImageExtractStep,
        DebLayer,
        PasswdLayer,
        GroupLayer,
        CaCertsLayer,
    ]


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromPullImagesRequest),
    ]
