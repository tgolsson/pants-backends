import os
from dataclasses import dataclass

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import (
    BinaryShims,
    BinaryShimsRequest,
    SystemBinariesSubsystem,
)
from pants.engine.fs import AddPrefix, Digest, MergeDigests, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import GeneratedSources, GenerateSourcesRequest
from pants.engine.unions import UnionRule
from pants.source.source_root import SourceRoot, SourceRootRequest

from pants_backend_k8s.target_types import KubernetesSourceField
from pants_backend_kustomize.subsystem import KustomizeTool
from pants_backend_kustomize.target_types import KustomizeSourcesField
from pants_backend_kustomize.util_rules.prepare_context import (
    KustomizationContext,
    KustomizationContextRequest,
)


@dataclass(frozen=True)
class GitRequest:
    pass


@rule
async def get_binary_shims(
    _: GitRequest, system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware
) -> BinaryShims:
    kwargs = dict(
        rationale="asdqwe",
        search_path=system_binaries_subsystem.system_binary_paths,
    )

    binary_shims = BinaryShimsRequest.for_binaries(
        "git",
        **kwargs,
    )

    return await Get(BinaryShims, BinaryShimsRequest, binary_shims)


class GenerateKubernetesFromKustomizeRequest(GenerateSourcesRequest):
    input = KustomizeSourcesField
    output = KubernetesSourceField


@rule
async def generate_kubernetes_from_kustomize(
    request: GenerateKubernetesFromKustomizeRequest,
    kustomize: KustomizeTool,
    platform: Platform,
) -> GeneratedSources:
    context, kustomize, git = await MultiGet(
        Get(KustomizationContext, KustomizationContextRequest(request.protocol_target)),
        Get(
            DownloadedExternalTool,
            ExternalToolRequest,
            kustomize.get_request(platform),
        ),
        Get(BinaryShims, GitRequest()),
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
            env={"PATH": git.path_component},
            input_digest=merged_digest,
            immutable_input_digests=git.immutable_input_digests,
            description=f"Generating Kustomize sources from {request.protocol_target.address}.",
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
