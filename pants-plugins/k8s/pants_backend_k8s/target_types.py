from __future__ import annotations

from dataclasses import dataclass

from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    SingleSourceField,
    StringField,
    Target,
)
from pants.util.strutil import softwrap


class KubernetesSourceField(SingleSourceField):
    expected_file_extensions = (".yaml", ".yml", ".json")
    alias = "source"


class KubernetesSourceTarget(Target):
    alias = "k8s_source"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesSourceField,
    )
    help = "A single Kubernetes source file."


class KubernetesCommandField(StringField):
    alias = "command"
    default = "describe"
    valid_choices = ("apply", "describe", "delete", "replace", "create", "get")
    help = softwrap("""The command to run against the provided resource.""")


class KubernetesNamespaceField(StringField):
    alias = "namespace"
    default = None
    help = softwrap("""The namespace to run in.""")


class KubernetesTemplateDependency(Dependencies):
    help = "the name of the dependency to use as the template"
    alias = "template"


class KubernetesClusterField(StringField):
    alias = "cluster"
    help = softwrap(
        """
        The target cluster/context to run on. If not provided; the command cannot be ran.
        """
    )


class KubernetesKindField(StringField):
    alias = "kind"
    help = softwrap(
        """
        A descriptor of the kinds included.
        """
    )


class KubernetesTarget(Target):
    alias = "kubernetes"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesTemplateDependency,
        KubernetesKindField,
        KubernetesClusterField,
        KubernetesCommandField,
        KubernetesNamespaceField,
    )
    help = "A kubernetes target."


@dataclass(frozen=True)
class KubernetesCommandProcessRequest:
    target: Target


@dataclass(frozen=True)
class KubernetesCommandLineProcessRequest:
    target: Target


class KubernetesTargetBundleDependencies(Dependencies):
    help = "the name of the dependency to use as the template"
    alias = "objects"


class KubernetesTargetBundle(Target):
    alias = "kubernetes"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesTargetBundleDependencies,
        KubernetesCommandField,
    )
    help = "A kubernetes target."


def targets():
    return [
        KubernetesSourceTarget,
        KubernetesTarget,
    ]
