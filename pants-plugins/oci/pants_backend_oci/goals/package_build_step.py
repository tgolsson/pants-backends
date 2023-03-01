# Copyright 2022 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass
from typing import ClassVar

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSet,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    InvalidTargetException,
    Target,
    Targets,
    WrappedTarget,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import SkopeoTool
from pants_backend_oci.target_types import (
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
    ImageDigest,
    ImageRepository,
    ImageTag,
)
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

    process = await Get(
        Process,
        ImageArtifactBuildRequest(ImageArtifactBuildRequest.field_set_type.create(target)),
    )

    output = await Get(ProcessResult, Process, process)
    artifact = BuiltPackageArtifact(
        relpath="qwe",
        extra_log_lines=(f"Built artifacts: {output.digest}",),
    )

    return BuiltPackage(output.digest, (artifact,))


def rules():
    return [*collect_rules(), UnionRule(PackageFieldSet, ImageArtifactBuildFieldSet)]
