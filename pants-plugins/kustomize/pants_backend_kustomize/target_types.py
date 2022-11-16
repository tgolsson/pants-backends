from __future__ import annotations

from pants.engine.rules import collect_rules
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    MultipleSourcesField,
    SingleSourceField,
    Target,
)
from pants.util.strutil import softwrap


class KustomizeDependenciesField(Dependencies):
    """Dependencies such as packages, secret-decryptors, docker-push-shas, that should be embedded
    into the generated manifest.

    """

    pass


class KustomizeSourceField(SingleSourceField):
    """A file included in a kustomize run. Likely a .yml or .yaml, but could also be other artifacts
    consumed by secret/cm generators."""

    expected_file_extensions = (".yaml", ".yml")
    uses_source_roots = False

    help = softwrap(
        """
        A source file included in a kustomize run. Most likely this'll be other .yaml or .yml files,
        but any other resource that can be put into Kubernetes is valid.
        """
    )


class KustomizeSourcesField(MultipleSourcesField):
    """A set of files feeding into kustomize."""

    alias = "sources"
    uses_source_roots = False

    help = softwrap(
        """
        A group of files used in a kustomize target.
        """
    )


class KustomizeTarget(Target):
    alias = "kustomize"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KustomizeDependenciesField,
        KustomizeSourcesField,
    )
    help = softwrap(
        """A target for converting a kustomization.yaml and dependencies into a YAML document for
        use with kubectl.

        Example BUILD file:

            kustomize(
                name="kustomize",
                root_src="kustomization.yaml",
                dependencies=[],
                sources=["namespace.yaml", "deployment.yaml", "service.yaml"],
            )
        """
    )


def targets():
    return [
        KustomizeTarget,
    ]


def rules():
    return [
        *collect_rules(),
    ]
