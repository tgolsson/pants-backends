import os
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import CreateDigest, Digest, DigestContents, FileContent, MergeDigests
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    DependenciesRequest,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    Target,
    Targets,
)

from pants_backend_kustomize.target_types import (
    KustomizeDependenciesField,
    KustomizeSourcesField,
    KustomizeTarget,
)


@dataclass(frozen=True)
class KustomizationContextRequest:
    target: Target


@dataclass(frozen=True)
class KustomizationContext:
    digest: Digest


@rule
async def prepare_build_context(request: KustomizationContextRequest) -> KustomizationContext:
    root_get = Get(
        SourceFiles,
        SourceFilesRequest([request.target[KustomizeSourcesField]]),
    )

    root_dependencies = await Get(
        Targets,
        DependenciesRequest(request.target[KustomizeDependenciesField]),
    )

    root_contexts_get = []
    other_deps = []
    for dependency in root_dependencies:
        if isinstance(dependency, KustomizeTarget):
            root_contexts_get.append(
                Get(KustomizationContext, KustomizationContextRequest(target=dependency))
            )
        else:
            other_deps.append(dependency)

    embedded_pkgs_per_target_request = Get(
        FieldSetsPerTarget,
        FieldSetsPerTargetRequest(PackageFieldSet, other_deps),
    )

    (root, embedded_pkgs_per_target, *bases) = await MultiGet(
        root_get,
        embedded_pkgs_per_target_request,
        *root_contexts_get,
    )

    # Package binary dependencies for build context.
    embedded_pkgs = await MultiGet(
        Get(BuiltPackage, PackageFieldSet, field_set)
        for field_set in embedded_pkgs_per_target.field_sets
    )

    root_contents = await Get(DigestContents, Digest, root.snapshot.digest)
    root_content = root_contents[0]
    root_dir = os.path.dirname(root_content.path)
    root_manifest = root_content.content.decode()

    embedded_pkgs = embedded_pkgs or []
    if request.target[KustomizeDependenciesField].value:
        for dep, pkg in zip(request.target[KustomizeDependenciesField].value, embedded_pkgs):
            root_manifest = root_manifest.replace(
                dep, os.path.relpath(pkg.artifacts[0].relpath, root_dir)
            )

    patched_root = await Get(
        Digest, CreateDigest([FileContent(root_content.path, root_manifest.encode())])
    )

    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                patched_root,
                root.snapshot.digest,
                *[built_package.digest for built_package in embedded_pkgs],
                *[base.digest for base in bases],
            ]
        ),
    )

    return KustomizationContext(input_digest)


def rules():
    return collect_rules()
