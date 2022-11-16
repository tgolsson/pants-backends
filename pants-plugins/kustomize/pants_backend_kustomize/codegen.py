import os

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.target_types import FileSourceField
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import (
    AddPrefix,
    CreateDigest,
    Digest,
    DigestContents,
    FileContent,
    MergeDigests,
    RemovePrefix,
    Snapshot,
)
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    DependenciesRequest,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    GeneratedSources,
    GenerateSourcesRequest,
    SourcesField,
    Target,
    Targets,
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.source.source_root import SourceRoot, SourceRootRequest
from pants_backend_k8s.target_types import KubernetesSourceField

from pants_backend_kustomize.subsystem import KustomizeTool
from pants_backend_kustomize.target_types import KustomizeSourcesField
from pants_backend_kustomize.util_rules.prepare_context import (
    KustomizationContext,
    KustomizationContextRequest,
)


class GenerateKubernetesFromKustomizeRequest(GenerateSourcesRequest):
    input = KustomizeSourcesField
    output = KubernetesSourceField


@rule
async def generate_kubernetes_from_kustomize(
    request: GenerateKubernetesFromKustomizeRequest, kustomize: KustomizeTool
) -> GeneratedSources:
    (context, kustomize) = await MultiGet(
        Get(KustomizationContext, KustomizationContextRequest(request.protocol_target)),
        Get(
            DownloadedExternalTool,
            ExternalToolRequest,
            kustomize.get_request(Platform.current),
        ),
    )

    merged_digest = await Get(
        Digest,
        MergeDigests(
            [context.digest, kustomize.digest],
        ),
    )

    root_dir = request.protocol_target.address.spec_path
    output_files = os.path.join(root_dir, f"{request.protocol_target.address.target_name}.yaml")

    result = await Get(
        ProcessResult,
        Process(
            (
                kustomize.exe,
                "build",
                "--load-restrictor",
                "LoadRestrictionsNone",
                "-o",
                output_files,
                root_dir,
            ),
            input_digest=merged_digest,
            description=f"Generating Python sources from {request.protocol_target.address}.",
            output_files=(output_files,),
        ),
    )

    source_root_request = SourceRootRequest.for_target(request.protocol_target)
    source_root = await Get(SourceRoot, SourceRootRequest, source_root_request)
    source_root_restored = (
        await Get(Snapshot, AddPrefix(result.output_digest, source_root.path))
        if source_root.path != "."
        else await Get(Snapshot, Digest, result.output_digest)
    )

    return GeneratedSources(source_root_restored)


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateSourcesRequest, GenerateKubernetesFromKustomizeRequest),
    ]
