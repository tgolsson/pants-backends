from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior, RunRequest
from pants.core.util_rules.environments import EnvironmentNameRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.environment import EnvironmentName
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
from pants.engine.unions import UnionMembership

from pants_backend_k8s.subsystem import KubernetesTool
from pants_backend_k8s.target_types import (
    KubeconfigDependencyField,
    KubernetesClusterField,
    KubernetesCommandField,
    KubernetesContextField,
    KubernetesNamespaceField,
    KubernetesSourceField,
    KubernetesTarget,
    KubernetesTargetBundleDependencies,
    KubernetesTemplateDependency,
    KubernetesUserField,
)
from pants_backend_k8s.util.kubeconfig import (
    KubeconfigRequest,
    KubeconfigRequestRequest,
    KubeconfigRequestWrap,
    KubeconfigResponse,
)


@dataclass(frozen=True)
class KubernetesCommandProcessRequest:
    target: Target


@dataclass(frozen=True)
class KubernetesCommandLineProcessRequest:
    target: Target


class RunKubernetesCommand(RunFieldSet):
    required_fields = (KubernetesClusterField,)
    run_in_sandbox_behavior = RunInSandboxBehavior.RUN_REQUEST_HERMETIC


@rule
async def compute_command_line(
    request: KubernetesCommandLineProcessRequest, tool: KubernetesTool, platform: Platform
) -> Process:
    kubernetes_command = request.target
    download_kubernetes_get = Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform))

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
    request: KubernetesCommandProcessRequest,
    tool: KubernetesTool,
    platform: Platform,
    unions: UnionMembership,
) -> Process:
    kubernetes_command: KubernetesTarget = request.target

    kubeconfig_address = await Get(
        Addresses,
        UnparsedAddressInputs(
            kubernetes_command[KubeconfigDependencyField].value,
            owning_address=request.target.address,
            description_of_origin="asd",
        ),
    )

    deps, kubeconfig_target, environment_name = await MultiGet(
        Get(Targets, DependenciesRequest(kubernetes_command[KubernetesTemplateDependency])),
        Get(
            WrappedTarget,
            WrappedTargetRequest(
                kubeconfig_address[0],
                description_of_origin="kubectl run",
            ),
        ),
        Get(
            EnvironmentName,
            EnvironmentNameRequest,
            EnvironmentNameRequest.from_target(request.target),
        ),
    )

    kubeconfig_request = await Get(KubeconfigRequestWrap, KubeconfigRequestRequest(kubeconfig_target.target))

    sources, kubeconfig, tool = await MultiGet(
        Get(
            SourceFiles,
            SourceFilesRequest(
                sources_fields=[d.get(SourcesField) for d in deps],
                for_sources_types=(KubernetesSourceField,),
                enable_codegen=True,
            ),
        ),
        Get(
            KubeconfigResponse,
            {kubeconfig_request.request: KubeconfigRequest, environment_name: EnvironmentName},
        ),
        Get(DownloadedExternalTool, ExternalToolRequest, tool.get_request(platform)),
    )

    args = {}
    digests = [sources.snapshot.digest, tool.digest]

    if kubeconfig.digest:
        digests.append(kubeconfig.digest)
        args["--kubeconfig"] = f"{{chroot}}/{kubeconfig.path}"

    else:
        args["--kubeconfig"] = kubeconfig.path

    input_digest = await Get(Digest, MergeDigests(digests))
    if kubeconfig.namespace:
        args["--namespace"] = kubeconfig.namespace

    if kubernetes_command[KubernetesNamespaceField].value:
        args["--namespace"] = kubeconfig.namespace

    if kubeconfig.cluster:
        args["--cluster"] = kubeconfig.cluster

    if kubernetes_command[KubernetesClusterField].value:
        args["--cluster"] = kubeconfig.cluster

    if kubeconfig.context:
        args["--context"] = kubeconfig.context

    if kubernetes_command[KubernetesContextField].value:
        args["--context"] = kubeconfig.context

    if kubeconfig.user:
        args["--user"] = kubeconfig.user

    if kubernetes_command[KubernetesUserField].value:
        args["--user"] = kubeconfig.user

    flat = [item for pair in args.items() for item in pair]

    target_file = sources.files[0]
    command = (
        f"{{chroot}}/{tool.exe}",
        kubernetes_command[KubernetesCommandField].value,
        "-f",
        f"{{chroot}}/{target_file}",
        *flat,
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
    )


@dataclass(frozen=True)
class KubernetesTargetBundleCommandProcessRequest:
    target: Target


class RunKubernetesTargetBundleCommand(RunFieldSet):
    required_fields = (KubernetesTargetBundleDependencies,)
    run_in_sandbox_behavior = RunInSandboxBehavior.RUN_REQUEST_HERMETIC


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
    )


def rules():
    rules = [
        *collect_rules(),
        *RunKubernetesCommand.rules(),
        *RunKubernetesTargetBundleCommand.rules(),
    ]

    return rules
