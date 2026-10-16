import os
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import CreateDigest, Digest, FileContent, MergeDigests
from pants.engine.internals.graph import find_valid_field_sets, resolve_targets
from pants.engine.intrinsics import create_digest, get_digest_contents, merge_digests
from pants.engine.rules import Get, collect_rules, concurrently, implicitly, rule
from pants.engine.target import DependenciesRequest, FieldSetsPerTargetRequest, Target

from pants_backend_kustomize.requests import (
    KustomizeInjectData,
    KustomizeInjectRequest,
    KustomizeInjectRequestQuery,
    kirr_to_kir,
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
async def prepare_build_context(
    request: KustomizationContextRequest,
) -> KustomizationContext:
    root_get = determine_source_files(SourceFilesRequest([request.target[KustomizeSourcesField]]))

    root_dependencies = await resolve_targets(
        **implicitly(DependenciesRequest(request.target[KustomizeDependenciesField]))
    )

    root_contexts_get = []
    other_deps = []
    dep_names = []
    inject_data_queries = []
    for dependency, dependency_name in zip(
        root_dependencies, request.target[KustomizeDependenciesField].value or []
    ):
        kir = await kirr_to_kir(KustomizeInjectRequestQuery(dependency), **implicitly())
        if isinstance(dependency, KustomizeTarget):
            root_contexts_get.append(prepare_build_context(KustomizationContextRequest(target=dependency)))
        elif kir.valid:
            query = await Get(KustomizeInjectData, KustomizeInjectRequest, kir.request)
            inject_data_queries.append(query)
        else:
            other_deps.append(dependency)
            dep_names.append(dependency_name)

    embedded_pkgs_per_target_request = find_valid_field_sets(
        FieldSetsPerTargetRequest(PackageFieldSet, other_deps), **implicitly()
    )

    (root, embedded_pkgs_per_target, *bases) = await concurrently(
        root_get,
        embedded_pkgs_per_target_request,
        *root_contexts_get,
    )

    # Package binary dependencies for build context.
    embedded_pkgs = await concurrently(
        Get(BuiltPackage, PackageFieldSet, field_set) for field_set in embedded_pkgs_per_target.field_sets
    )

    root_contents = await get_digest_contents(root.snapshot.digest)
    root_content = None
    other_contents = []
    for file in root_contents:
        if file.path.endswith("kustomization.yaml"):
            root_content = file

        else:
            other_contents.append(file)

    if root_content is None:
        raise Exception("no kustomization.yaml file in build context")

    root_dir = os.path.dirname(root_content.path)
    root_manifest = root_content.content.decode()

    embedded_pkgs = embedded_pkgs or []
    for dep, pkg in zip(dep_names, embedded_pkgs):
        root_manifest = root_manifest.replace(dep, os.path.relpath(pkg.artifacts[0].relpath, root_dir))

    for kustomize_inject in inject_data_queries:
        root_manifest = root_manifest.replace(f"//{kustomize_inject.address}", kustomize_inject.value)

    patched_root = await create_digest(
        CreateDigest([FileContent(root_content.path, root_manifest.encode()), *other_contents])
    )

    input_digest = await merge_digests(
        MergeDigests(
            [
                patched_root,
                *[built_package.digest for built_package in embedded_pkgs],
                *[base.digest for base in bases],
            ]
        )
    )

    return KustomizationContext(input_digest)


def rules():
    return collect_rules()
