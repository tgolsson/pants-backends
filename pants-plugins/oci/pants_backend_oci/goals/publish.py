# Copyright 2022 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass

from pants.core.goals.publish import (
    PublishFieldSet,
    PublishOutputData,
    PublishPackages,
    PublishProcesses,
    PublishRequest,
)
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.env_vars import EnvironmentVars as Environment
from pants.engine.env_vars import EnvironmentVarsRequest as EnvironmentRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get
from pants.engine.platform import Platform
from pants.engine.process import InteractiveProcess, Process
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import SkopeoTool
from pants_backend_oci.target_types import ImageRepository, ImageTag


@dataclass(frozen=True)
class PublishImageRequest(PublishRequest):
    pass


@dataclass(frozen=True)
class PublishImageFieldSet(PublishFieldSet):
    publish_request_type = PublishImageRequest
    required_fields = (
        ImageRepository,
        ImageTag,
    )

    repository: ImageRepository
    tag: ImageTag

    def get_output_data(self) -> PublishOutputData:
        return PublishOutputData(
            {
                "publisher": "skopeo",
                **super().get_output_data(),
            }
        )


@dataclass(frozen=True)
class OciPublishProcessRequest:
    input_digest: Digest
    repository: str
    tag: str
    description: str

    directory: str


@rule(desc="Convert OCI Image to Archive", level=LogLevel.DEBUG)
async def publish_oci_process(
    request: OciPublishProcessRequest, skopeo: SkopeoTool, platform: Platform
) -> Process:
    skopeo = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        skopeo.get_request(platform),
    )
    sandbox_input = await Get(Digest, MergeDigests([skopeo.digest, request.input_digest]))
    relevant_env = await Get(Environment, EnvironmentRequest(["HOME", "PATH", "XDG_RUNTIME_DIR"]))
    return Process(
        input_digest=sandbox_input,
        argv=(
            skopeo.exe,
            # TODO[TSOL]: Should likely provide a way to inject a
            # policy into this... Maybe dependency injector?
            "--insecure-policy",
            "copy",
            f"oci:{request.directory}",
            f"docker://{request.repository}:{request.tag}",
        ),
        description=f"{request.repository}:{request.tag}",
        output_files=tuple(),
        env=relevant_env,
    )


@rule(desc="Publish OCI Image")
async def publish_oci_image(request: PublishImageRequest) -> PublishProcesses:
    field_set = request.field_set

    package = request.packages[0]
    metadata = package.artifacts[0]

    if field_set.repository.value is None:
        return PublishProcesses(
            (PublishPackages(names=(f"{field_set.address}",), description="(because it has no repository)"),)
        )

    process = await Get(
        Process,
        OciPublishProcessRequest(
            input_digest=package.digest,
            repository=field_set.repository.value,
            tag=field_set.tag.value or "latest",
            description=(
                f"Publish OCI Image {field_set.address} -> {field_set.repository.value}:{field_set.tag.value}"
            ),
            directory=metadata.relpath,
        ),
    )

    return PublishProcesses(
        (
            PublishPackages(
                names=(f"{metadata.sha}",),
                process=InteractiveProcess.from_process(process),
                description=process.description,
                data=PublishOutputData({"repository": process.description}),
            ),
        )
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PublishFieldSet, PublishImageFieldSet),
        UnionRule(PublishRequest, PublishImageRequest),
    ]
