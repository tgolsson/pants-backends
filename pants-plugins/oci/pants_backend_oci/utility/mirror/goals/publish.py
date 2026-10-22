from dataclasses import dataclass

from pants.core.goals.publish import (
    PublishFieldSet,
    PublishOutputData,
    PublishPackages,
    PublishProcesses,
    PublishRequest,
)
from pants.core.util_rules.env_vars import environment_vars_subset
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.env_vars import EnvironmentVarsRequest as EnvironmentRequest
from pants.engine.platform import Platform
from pants.engine.process import InteractiveProcess, Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import CraneTool
from pants_backend_oci.utility.mirror.targets import (
    DestinationRepository,
    ImageDigest,
    ImageTag,
    SourceRepository,
)


@dataclass(frozen=True)
class MirrorImageRequest(PublishRequest):
    pass


@dataclass(frozen=True)
class MirrorImageFieldSet(PublishFieldSet):
    publish_request_type = MirrorImageRequest
    required_fields = (
        SourceRepository,
        DestinationRepository,
        ImageDigest,
    )

    source: SourceRepository
    destination: DestinationRepository
    digest: ImageDigest
    tag: ImageTag

    def get_output_data(self) -> PublishOutputData:
        return PublishOutputData(
            {
                "publisher": "crane",
                **super().get_output_data(),
            }
        )


@dataclass(frozen=True)
class OciMirrorProcessRequest:
    digest: str
    source: str
    tag: str
    destination: str


@rule(desc="Mirror OCI Image", level=LogLevel.DEBUG)
async def oci_mirror_process(
    request: OciMirrorProcessRequest, crane: CraneTool, platform: Platform
) -> Process:
    crane = await download_external_tool(crane.get_request(platform))
    relevant_env = await environment_vars_subset(
        EnvironmentRequest(CraneTool.registry_env_vars), **implicitly()
    )

    # Without a tag crane pushes the mirror as `latest`.
    destination = request.destination
    if request.tag:
        destination = f"{destination}:{request.tag}"

    # crane copies the full manifest list and keeps the digest, so the mirror resolves
    # `destination@sha256:<digest>` exactly like the source.
    argv = (
        crane.exe,
        "copy",
        f"{request.source}@sha256:{request.digest}",
        destination,
    )

    return Process(
        input_digest=crane.digest,
        argv=argv,
        description=f"{request.destination}",
        output_files=tuple(),
        env=relevant_env,
    )


@rule(desc="Mirror OCI Image")
async def mirror_oci_image(request: MirrorImageRequest) -> PublishProcesses:
    field_set = request.field_set

    process = await oci_mirror_process(
        OciMirrorProcessRequest(
            destination=field_set.destination.value,
            source=field_set.source.value,
            tag=field_set.tag.value,
            digest=field_set.digest.value,
        ),
        **implicitly(),
    )

    return PublishProcesses(
        (
            PublishPackages(
                names=(f"{field_set.source.value}:{field_set.digest.value}",),
                process=InteractiveProcess.from_process(process),
                description=process.description,
                data=PublishOutputData({"repository": process.description}),
            ),
        )
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PublishFieldSet, MirrorImageFieldSet),
        UnionRule(PublishRequest, MirrorImageRequest),
    ]
