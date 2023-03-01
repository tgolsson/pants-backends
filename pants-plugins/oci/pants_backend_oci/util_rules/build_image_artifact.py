"""

"""
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
from pants_backend_oci.target_types import (
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
)
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
)


@dataclass(frozen=True)
class ImageArtifactPullFieldSet(FieldSet):
    required_fields = (ImageBase, ImageBuildCommand, ImageBuildOutputs)

    base: ImageBase

    commands: ImageBuildCommand
    outputs: ImageBuildOutputs

    dependencies: ImageDependencies


class ImageBundlePullRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageBundlePullFieldSet
