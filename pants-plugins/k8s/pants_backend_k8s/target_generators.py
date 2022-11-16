from __future__ import annotations

from dataclasses import dataclass

from pants.backend.shell.target_types import (
    ShellCommandCommandField,
    ShellCommandRunTarget,
)
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    GeneratedTargets,
    GenerateTargetsRequest,
    Target,
    TargetGenerator,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.strutil import softwrap

from pants_backend_k8s.target_types import (
    KubernetesClusterField,
    KubernetesCommandField,
    KubernetesKindField,
    KubernetesNamespaceField,
    KubernetesTarget,
    KubernetesTargetBundle,
    KubernetesTargetBundleDependencies,
    KubernetesTemplateDependency,
)


class KubernetesTargetGenerator(TargetGenerator):
    alias = "k8s_object"
    help = softwrap(
        """
        Generate `kubernetes` targets with all specific commands.
        """
    )
    generated_target_cls = KubernetesTarget
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesTemplateDependency,
        KubernetesKindField,
        KubernetesClusterField,
        KubernetesCommandField,
        KubernetesNamespaceField,
    )
    copied_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesKindField,
        KubernetesClusterField,
        KubernetesNamespaceField,
    )
    moved_fields = (KubernetesCommandField, KubernetesTemplateDependency)


class GenerateFromKubernetesRequest(GenerateTargetsRequest):
    generate_from = KubernetesTargetGenerator


@rule
def generate_from_k8s_object(
    request: GenerateFromKubernetesRequest, union_membership: UnionMembership
) -> GeneratedTargets:
    generator = request.generator

    def create_tgt(command: str) -> KubernetesTarget:
        return KubernetesTarget(
            {
                KubernetesCommandField.alias: command,
                KubernetesTemplateDependency.alias: request.template["template"],
                **request.template,
            },
            request.template_address.create_generated(command),
            union_membership,
        )

    result = [create_tgt(c) for c in ("apply", "describe", "delete", "get", "replace", "create")]

    return GeneratedTargets(generator, result)


@dataclass(frozen=True)
class KubernetesTargetBundleCommandProcessRequest:
    target: Target


class KubernetesTargetBundleGenerator(TargetGenerator):
    alias = "k8s_objects"
    help = softwrap(
        """
        Generate `kubernetes` targets with all specific commands.
        """
    )
    generated_target_cls = ShellCommandRunTarget
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesTargetBundleDependencies,
    )
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (KubernetesTargetBundleDependencies,)


class GenerateFromKubernetesTargetBundleRequest(GenerateTargetsRequest):
    generate_from = KubernetesTargetBundleGenerator


@rule
async def generate_from_k8s_objects(
    request: GenerateFromKubernetesTargetBundleRequest,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    generator = request.generator

    def target_to_pathed_target(dep):
        if dep.startswith("//"):
            return dep

        return f"//{generator.address.spec_path}{dep}"

    paths = [target_to_pathed_target(d) for d in request.template["objects"]]

    async def create_tgt(command: str) -> KubernetesTargetBundle:
        command_lines = [f"./pants run {p}#{command}" for p in paths]
        cli = "export PANTS_CONCURRENT=true ; " + " ; ".join(command_lines)
        return ShellCommandRunTarget(
            {
                ShellCommandCommandField.alias: cli,
            },
            request.template_address.create_generated(command),
            union_membership,
        )

    result = [
        await create_tgt(c) for c in ("apply", "describe", "delete", "get", "replace", "create")
    ]

    return GeneratedTargets(generator, result)


def targets():
    return [
        KubernetesTargetGenerator,
        KubernetesTargetBundleGenerator,
    ]


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromKubernetesRequest),
        UnionRule(GenerateTargetsRequest, GenerateFromKubernetesTargetBundleRequest),
    ]
