from __future__ import annotations

from pants.engine.rules import collect_rules
from pants.engine.target import COMMON_TARGET_FIELDS, Dependencies, MultipleSourcesField, Target
from pants.util.strutil import softwrap


class KustomizeDependenciesField(Dependencies):
    """Dependencies such as packages, secret-decryptors, docker-push-shas, that should be embedded
    into the generated manifest.

    """

    pass


class KustomizeSourcesField(MultipleSourcesField):
    """A set of files feeding into kustomize."""

    alias = "sources"
    uses_source_roots = False

    default = ["*.yaml", "*.yml"]
    help = softwrap("""
        A group of files used in a kustomize target.
        """)


class KustomizeTarget(Target):
    alias = "kustomize"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        KustomizeDependenciesField,
        KustomizeSourcesField,
    )
    help = softwrap("""A target for converting a kustomization.yaml and dependencies into a YAML document for
        use with kubectl.

        Example BUILD file:

            kustomize(
                name="kustomize",
                root_src="kustomization.yaml",
                dependencies=[],
                sources=["namespace.yaml", "deployment.yaml", "service.yaml"],
            )
        """)


def targets():
    return [
        KustomizeTarget,
    ]


def rules():
    return [
        *collect_rules(),
    ]
