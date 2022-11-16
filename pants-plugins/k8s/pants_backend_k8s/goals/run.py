from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.run import RunDebugAdapterRequest, RunFieldSet, RunRequest
from pants.core.util_rules.external_tool import (
    DownloadedExternalTool,
    ExternalToolRequest,
)
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import EMPTY_DIGEST, Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    DependenciesRequest,
    SourcesField,
    Target,
    Targets,
    WrappedTarget,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule

from pants_backend_k8s.subsystem import KubernetesTool
from pants_backend_k8s.target_types import (
    KubernetesClusterField,
    KubernetesCommandField,
    KubernetesNamespaceField,
    KubernetesSourceField,
    KubernetesTarget,
    KubernetesTargetBundleDependencies,
    KubernetesTemplateDependency,
)


@dataclass(frozen=True)
class KubernetesCommandProcessRequest:
    target: Target


@dataclass(frozen=True)
class KubernetesCommandLineProcessRequest:
    target: Target


class RunKubernetesCommand(RunFieldSet):
    required_fields = (KubernetesClusterField,)


@rule
async def compute_command_line(
    request: KubernetesCommandLineProcessRequest, tool: KubernetesTool
) -> Process:
    kubernetes_command = request.target
    download_kubernetes_get = Get(
        DownloadedExternalTool, ExternalToolRequest, tool.get_request(Platform.current)
    )

    deps = await Get(Targets, DependenciesRequest(kubernetes_command[KubernetesTemplateDependency]))
    (sources, tool) = await MultiGet(
        Get(
            SourceFiles,
            SourceFilesRequest(
                sources_fields=[d.get(SourcesField) for d in deps],
                for_sources_types=(KubernetesSourceField,),
                enable_codegen=True,
            ),
        ),
        download_kubernetes_get,
    )

    target_file = sources.files[0]

    command = [
        f"{{chroot}}/{tool.exe}",
        kubernetes_command[KubernetesCommandField].value,
        "-f",
        f"{{chroot}}/{target_file}",
        "--context",
        kubernetes_command[KubernetesClusterField].value,
    ]

    if kubernetes_command.has_field(KubernetesNamespaceField):
        command.extend(
            [
                "--namespace",
                kubernetes_command[KubernetesNamespaceField].value,
            ]
        )

    return Process(
        command,
        description=f"Running {kubernetes_command.alias} {kubernetes_command.address}",
        input_digest=EMPTY_DIGEST,
    )


@rule
async def prepare_kubernetes_command_process(
    request: KubernetesCommandProcessRequest, tool: KubernetesTool
) -> Process:
    download_kubernetes_get = Get(
        DownloadedExternalTool, ExternalToolRequest, tool.get_request(Platform.current)
    )

    kubernetes_command: KubernetesTarget = request.target
    deps = await Get(Targets, DependenciesRequest(kubernetes_command[KubernetesTemplateDependency]))

    (sources, tool) = await MultiGet(
        Get(
            SourceFiles,
            SourceFilesRequest(
                sources_fields=[d.get(SourcesField) for d in deps],
                for_sources_types=(KubernetesSourceField,),
                enable_codegen=True,
            ),
        ),
        download_kubernetes_get,
    )

    target_file = sources.files[0]
    work_dir = EMPTY_DIGEST
    input_digest = await Get(Digest, MergeDigests([sources.snapshot.digest, work_dir, tool.digest]))

    command = (
        f"{{chroot}}/{tool.exe}",
        kubernetes_command[KubernetesCommandField].value,
        "-f",
        f"{{chroot}}/{target_file}",
        "--context",
        kubernetes_command[KubernetesClusterField].value,
    )

    return Process(
        command,
        description=f"Running {kubernetes_command.alias} {kubernetes_command.address}",
        input_digest=input_digest,
    )


@rule
async def run_kubernetes_command_target(request: RunKubernetesCommand) -> RunRequest:
    wrapped_tgt = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.address, description_of_origin="<infallible>"),
    )
    process = await Get(Process, KubernetesCommandProcessRequest(wrapped_tgt.target))
    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
    )


@rule
async def run_kubernetes_command_target_debug(
    field_set: RunKubernetesCommand,
) -> RunDebugAdapterRequest:
    raise NotImplementedError("Cannot run kubernetes commands in debug mode.")


@dataclass(frozen=True)
class KubernetesTargetBundleCommandProcessRequest:
    target: Target


class RunKubernetesTargetBundleCommand(RunFieldSet):
    required_fields = (KubernetesTargetBundleDependencies,)


@rule
async def run_kubernetes_target_bundle_command_target(
    request: RunKubernetesTargetBundleCommand,
) -> RunRequest:
    wrapped_tgt = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.address, description_of_origin="<infallible>"),
    )

    process = await Get(Process, KubernetesCommandProcessRequest(wrapped_tgt.target))
    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(RunFieldSet, RunKubernetesCommand),
    ]
