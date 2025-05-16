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
                "publisher": "skopeo",
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
    request: OciMirrorProcessRequest, skopeo: SkopeoTool, platform: Platform
) -> Process:
    skopeo = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        skopeo.get_request(platform),
    )
    sandbox_input = await Get(Digest, MergeDigests([skopeo.digest]))
    relevant_env = await Get(Environment, EnvironmentRequest(["HOME", "PATH", "XDG_RUNTIME_DIR"]))

    destination = f"docker://{request.destination}"
    if request.tag:
        destination = f"{destination}:{request.tag}"

    argv = (
        skopeo.exe,
        # TODO[TSOL]: Should likely provide a way to inject a
        # policy into this... Maybe dependency injector?
        "--insecure-policy",
        "copy",
        f"docker://{request.source}@sha256:{request.digest}",
        destination,
    )

    return Process(
        input_digest=sandbox_input,
        argv=argv,
        description=f"{request.destination}",
        output_files=tuple(),
        env=relevant_env,
    )


@rule(desc="Mirror OCI Image")
async def mirror_oci_image(request: MirrorImageRequest) -> PublishProcesses:
    field_set = request.field_set

    process = await Get(
        Process,
        OciMirrorProcessRequest(
            destination=field_set.destination.value,
            source=field_set.source.value,
            tag=field_set.tag.value,
            digest=field_set.digest.value,
        ),
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
