from __future__ import annotations

from dataclasses import dataclass

from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    SingleSourceField,
    StringField,
    StringSequenceField,
    Target,
)
from pants.util.strutil import softwrap


class KubeconfigClusterField()
class KubeconfigSourceField(SingleSourceField):
    alias = "config_file"
    default = None
    help = softwrap("""The kubeconfig to use.""")


class KubeconfigHostMarker(StringField):
    alias = "_kubeconfig_marker"
    default = None
    help = softwrap("""Marker field.""")


class ExtraBinariesField(StringSequenceField):
    alias = "extra_binaries"
    default = []

    help = softwrap(
        """
        Extra binaries to include in the sandbox. Useful if the kubectl command needs any authentication tools for example.
        """
    )


class HostKubeConfig(Target):
    alias = "host_kubeconfig"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubeconfigHostMarker,
        ExtraBinariesField,
    )
    help = "Will retrieve the Host kubeconfig file and expose to the sandbox."


class KubeConfig(Target):
    alias = "kubeconfig"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubeconfigSourceField,
        ExtraBinariesField,
    )

    help = softwrap(
        """Reads the kube config file and exposes it to the sandbox. If no config file is provided; the default config file will be used."""
    )


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
