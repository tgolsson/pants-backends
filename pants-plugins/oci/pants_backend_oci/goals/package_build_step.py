# Copyright 2022 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).


from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.engine.internals.selectors import Get
from pants.engine.process import ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import WrappedTarget, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.util_rules.build_image_artifact import (
    ImageArtifactBuildFieldSet,
    ImageArtifactBuildRequest,
)


@rule(desc="Package OCI Image", level=LogLevel.DEBUG)
async def package_oci_image(field_set: ImageArtifactBuildFieldSet) -> BuiltPackage:
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(field_set.address, description_of_origin="package_oci_image"),
    )
    target = wrapped_target.target

    output = await Get(
        ProcessResult,
        ImageArtifactBuildRequest(ImageArtifactBuildRequest.field_set_type.create(target)),
    )

    artifact = BuiltPackageArtifact(
        relpath="qwe",
        extra_log_lines=(f"Built artifacts: {output.output_digest}",),
    )

    return BuiltPackage(output.output_digest, (artifact,))


def rules():
    return [*collect_rules(), UnionRule(PackageFieldSet, ImageArtifactBuildFieldSet)]
