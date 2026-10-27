from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar

from pants.core.util_rules.env_vars import environment_vars_subset
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.env_vars import EnvironmentVars as Environment
from pants.engine.env_vars import EnvironmentVarsRequest as EnvironmentRequest
from pants.engine.fs import DigestSubset, PathGlobs
from pants.engine.intrinsics import digest_subset_to_digest, execute_process
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import CraneTool
from pants_backend_oci.target_types import (
    ImageArchitectureField,
    ImageDigest,
    ImageOsField,
    ImageRepository,
    ImageRepositoryAnonymous,
)
from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder


@dataclass(frozen=True)
class ImageBundlePullFieldSet(FieldSet):
    required_fields = (ImageDigest, ImageRepository)

    repository: ImageRepository
    digest: ImageDigest
    anonymous: ImageRepositoryAnonymous
    architecture: ImageArchitectureField
    os: ImageOsField


@dataclass(frozen=True)
class ImageBundlePullRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageBundlePullFieldSet


def crane_platform(request: ImageBundlePullRequest, platform: Platform) -> str:
    os = request.target.os.value or "linux"
    arch = request.target.architecture.value or ("arm64" if platform.value.endswith("arm64") else "amd64")
    return f"{os}/{arch}"


@rule
async def pull_oci_image(
    request: ImageBundlePullRequest,
    crane_tool: CraneTool,
    platform: Platform,
    builder: OciBuilder,
) -> FallibleImageBundle:
    crane = await download_external_tool(crane_tool.get_request(platform))
    image_ref = f"{request.target.repository.value}@sha256:{request.target.digest.value}"

    relevant_env = Environment()
    if not request.target.anonymous.value:
        relevant_env = await environment_vars_subset(
            EnvironmentRequest(CraneTool.registry_env_vars), **implicitly()
        )

    p = Process(
        argv=(
            crane.exe,
            "pull",
            "--format=oci",
            "--platform",
            crane_platform(request, platform),
            image_ref,
            "build",
        ),
        input_digest=crane.digest,
        description=f"Download OCI image {image_ref}",
        output_directories=("build",),
        env=relevant_env,
    )

    result = await execute_process(p, **implicitly())

    if result.exit_code != 0:
        return FallibleImageBundle(
            None, exit_code=result.exit_code, stderr=result.stderr.decode(), stdout=result.stdout.decode()
        )

    # crane leaves the manifest unnamed and in its registry format; the rest of the backend
    # addresses an OCI manifest as `build:build`.
    normalized = await execute_process(
        builder.process(
            oci_layout_builder,
            "normalize",
            input_digest=result.output_digest,
            description=f"Normalise OCI image {image_ref}",
            output_directories=("build",),
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )
    if normalized.exit_code != 0:
        error = normalized.stderr.decode(errors="replace").strip().removeprefix("error: ")
        return FallibleImageBundle(
            None, exit_code=normalized.exit_code, stderr=f"{error} ({image_ref})", stdout=""
        )

    image = json.loads(normalized.stdout)
    metadata = await digest_subset_to_digest(
        DigestSubset(
            normalized.output_digest,
            PathGlobs(
                [
                    "build/index.json",
                    "build/oci-layout",
                    *(f"build/blobs/{digest.replace(':', '/', 1)}" for digest in image["metadata"]),
                ]
            ),
        )
    )
    return FallibleImageBundle(
        ImageBundle(
            normalized.output_digest,
            f"sha256:{request.target.digest.value}",
            is_local=False,
            metadata=metadata,
        )
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, ImageBundlePullRequest),
    ]
