from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.core.util_rules.env_vars import environment_vars_subset
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.env_vars import EnvironmentVars as Environment
from pants.engine.env_vars import EnvironmentVarsRequest as EnvironmentRequest
from pants.engine.intrinsics import execute_process
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionRule

from pants_backend_oci.subsystem import SkopeoTool
from pants_backend_oci.target_types import (
    ImageArchitectureField,
    ImageDigest,
    ImageOsField,
    ImageRepository,
    ImageRepositoryAnonymous,
)
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
)


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


@rule
async def pull_oci_image(
    request: ImageBundlePullRequest, skopeo_tool: SkopeoTool, platform: Platform
) -> FallibleImageBundle:
    skopeo = await download_external_tool(skopeo_tool.get_request(platform))

    args = [
        skopeo.exe,
        # TODO[TSOL]: Should likely provide a way to inject a policy
        # into this... Maybe dependency injector?
        "--insecure-policy",
    ]

    if request.target.architecture.value:
        args.extend(["--override-arch", request.target.architecture.value])

    if request.target.os.value:
        args.extend(["--override-os", request.target.os.value])

    args.append("copy")

    relevant_env = Environment()
    if request.target.anonymous.value:
        args.append("--src-no-creds")

    else:
        relevant_env = await environment_vars_subset(
            EnvironmentRequest(["HOME", "PATH", "XDG_RUNTIME_DIR"]), **implicitly()
        )

    args.extend(
        [
            f"docker://{request.target.repository.value}@sha256:{request.target.digest.value}",
            "oci:build:build",
        ]
    )

    desc = f"Download OCI image {request.target.repository.value}@sha256:{request.target.digest.value}"

    p = Process(
        argv=tuple(args),
        input_digest=skopeo.digest,
        description=desc,
        output_directories=("build",),
        env=relevant_env,
    )

    result = await execute_process(p)

    if result.exit_code != 0:
        return FallibleImageBundle(
            None, exit_code=result.exit_code, stderr=result.stderr.decode(), stdout=result.stdout.decode()
        )

    return FallibleImageBundle(
        ImageBundle(result.output_digest, f"sha256:{request.target.digest.value}", is_local=False)
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, ImageBundlePullRequest),
    ]
