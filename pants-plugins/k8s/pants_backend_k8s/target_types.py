from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Address,
    Dependencies,
    InvalidFieldException,
    OptionalSingleSourceField,
    SingleSourceField,
    SpecialCasedDependencies,
    StringField,
    Target,
)
from pants.util.strutil import pluralize, softwrap


class KubeconfigClusterField(StringField):
    alias = "cluster"
    help = softwrap("""The default cluster to use with this kubeconfig.""")


class KubeconfigUserField(StringField):
    alias = "user"
    help = softwrap("""The default user to use with this kubeconfig.""")


class KubeconfigContextField(StringField):
    alias = "context"
    help = softwrap("""The default context to use with this kubeconfig.""")


class KubeconfigNamespaceField(StringField):
    alias = "namespace"
    help = softwrap("""The default namespace to use with this kubeconfig.""")


KUBECONFIG_COMMON_FIELDS = (
    KubeconfigClusterField,
    KubeconfigUserField,
    KubeconfigContextField,
    KubeconfigNamespaceField,
)


class KubeconfigSourceField(OptionalSingleSourceField):
    alias = "from_source"
    default = None
    help = softwrap("""A kubeconfig file that is checked in as a source file, or
        generated into the repository itself.""")


class KubeconfigGeneratedField(Dependencies):
    alias = "from_generator"
    default = None
    help = softwrap("""The kubeconfig file is generated as a single
        file from the rule""")


class KubeconfigHostMarker(StringField):
    alias = "_kubeconfig_marker"
    default = None
    help = softwrap("""Marker field.""")


class HostKubeConfig(Target):
    alias = "host_kubeconfig"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        *KUBECONFIG_COMMON_FIELDS,
        KubeconfigHostMarker,
    )
    help = "Will retrieve the Host kubeconfig file and expose to the sandbox."


class KubeConfig(Target):
    alias = "kubeconfig"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        *KUBECONFIG_COMMON_FIELDS,
        KubeconfigSourceField,
        KubeconfigGeneratedField,
    )

    help = softwrap("""Reads the kube config file and exposes it to the
        sandbox.""")


class KubeconfigDependencyField(SpecialCasedDependencies):
    alias = "kubeconfig"
    help = "The kubeconfig target to use for this target."

    @classmethod
    def compute_value(cls, raw_value: Optional[Iterable[str]], address: Address) -> Optional[Tuple[str, ...]]:
        value = super().compute_value(raw_value, address)

        if value is None:
            return None

        if len(value) > 1:
            pluralize(cls.expected_num_files, "file")
            raise InvalidFieldException(
                f"The {repr(cls.alias)} field in target {cls.address} must have "
                f"a single dependency, but it had {pluralize(len(value), 'dependencies')}."
            )

        return value


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
    help = softwrap("""
        The target cluster to run on. If not provided; the command cannot be ran.
        """)


class KubernetesContextField(StringField):
    alias = "context"
    help = softwrap("""
        The target context to run on. If not provided; the command cannot be ran.
        """)


class KubernetesUserField(StringField):
    alias = "user"
    help = softwrap("""
        The target user to run on. If not provided; the command cannot be ran.
        """)


class KubernetesKindField(StringField):
    alias = "kind"
    help = softwrap("""
        A descriptor of the kinds included.
        """)


class KubernetesTarget(Target):
    alias = "kubernetes"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KubernetesTemplateDependency,
        KubernetesKindField,
        KubernetesClusterField,
        KubernetesCommandField,
        KubernetesNamespaceField,
        KubernetesUserField,
        KubeconfigDependencyField,
        KubernetesContextField,
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
    return [KubernetesSourceTarget, KubernetesTarget, HostKubeConfig, KubeConfig]
