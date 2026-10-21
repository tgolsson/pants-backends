from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import ClassVar

from pants.core.util_rules.env_vars import environment_vars_subset
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.env_vars import EnvironmentVars as Environment
from pants.engine.env_vars import EnvironmentVarsRequest as EnvironmentRequest
from pants.engine.fs import CreateDigest, DigestSubset, FileContent, MergeDigests, PathGlobs
from pants.engine.intrinsics import (
    create_digest,
    digest_subset_to_digest,
    execute_process,
    get_digest_contents,
    merge_digests,
)
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionRule

from pants_backend_oci.subsystem import CraneTool
from pants_backend_oci.target_types import (
    ImageArchitectureField,
    ImageDigest,
    ImageOsField,
    ImageRepository,
    ImageRepositoryAnonymous,
)
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    ImageBundle,
)


@dataclass(frozen=True)
class ImageBundlePullFieldSet(FieldSet):
    required_fields = (ImageDigest, ImageRepository)

    repository: ImageRepository
    digest: ImageDigest
    anonymous: ImageRepositoryAnonymous
    architecture: ImageArchitectureField
    os: ImageOsField


@dataclass(frozen=True)
class ImageBundlePullRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageBundlePullFieldSet


# crane pulls Docker v2 images verbatim, but umoci only reads OCI manifests. The config and
# layer blobs are byte-compatible, so only the descriptors' media types need translating.
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_TO_OCI_MEDIA_TYPES = {
    DOCKER_MANIFEST: "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.container.image.v1+json": "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.image.rootfs.diff.tar.gzip": "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip": (
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip"
    ),
}


def _oci_media_type(media_type: str) -> str:
    if media_type.startswith("application/vnd.oci."):
        return media_type
    if media_type not in DOCKER_TO_OCI_MEDIA_TYPES:
        raise ValueError(f"Cannot convert media type {media_type!r} to OCI")
    return DOCKER_TO_OCI_MEDIA_TYPES[media_type]


def docker_manifest_to_oci(content: bytes) -> bytes:
    """Rewrite a Docker v2 image manifest as an OCI image manifest."""
    manifest = json.loads(content)
    manifest["mediaType"] = _oci_media_type(manifest["mediaType"])
    for descriptor in [manifest["config"], *manifest["layers"]]:
        descriptor["mediaType"] = _oci_media_type(descriptor["mediaType"])
    return json.dumps(manifest, separators=(",", ":")).encode()


def manifest_descriptor(index_content: bytes) -> dict:
    manifests = json.loads(index_content).get("manifests", [])
    if len(manifests) != 1:
        raise ValueError(f"Expected one manifest in the pulled OCI layout, found {len(manifests)}")
    return manifests[0]


def tag_layout_index(content: bytes, tag: str, manifest: bytes | None = None) -> bytes:
    """Name the single manifest in an OCI layout's `index.json` as `tag`.

    When `manifest` is given it replaces the manifest the index points at.
    """
    index = json.loads(content)
    descriptor = manifest_descriptor(content)
    index["manifests"] = [descriptor]
    if manifest is not None:
        parsed = json.loads(manifest)
        descriptor["mediaType"] = parsed["mediaType"]
        if "artifactType" in descriptor:
            descriptor["artifactType"] = parsed["config"]["mediaType"]
        descriptor["digest"] = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
        descriptor["size"] = len(manifest)

    descriptor.setdefault("annotations", {})["org.opencontainers.image.ref.name"] = tag
    return json.dumps(index, indent=2).encode()


def blob_path(digest: str) -> str:
    algorithm, hex_digest = digest.split(":", 1)
    return f"build/blobs/{algorithm}/{hex_digest}"


def crane_platform(request: ImageBundlePullRequest, platform: Platform) -> str:
    os = request.target.os.value or "linux"
    arch = request.target.architecture.value or ("arm64" if platform.value.endswith("arm64") else "amd64")
    return f"{os}/{arch}"


@rule
async def pull_oci_image(
    request: ImageBundlePullRequest, crane_tool: CraneTool, platform: Platform
) -> FallibleImageBundle:
    crane = await download_external_tool(crane_tool.get_request(platform))
    image_ref = f"{request.target.repository.value}@sha256:{request.target.digest.value}"

    relevant_env = Environment()
    if not request.target.anonymous.value:
        relevant_env = await environment_vars_subset(
            EnvironmentRequest(CraneTool.registry_env_vars), **implicitly()
        )

    p = Process(
        argv=(
            crane.exe,
            "pull",
            "--format=oci",
            "--platform",
            crane_platform(request, platform),
            image_ref,
            "build",
        ),
        input_digest=crane.digest,
        description=f"Download OCI image {image_ref}",
        output_directories=("build",),
        env=relevant_env,
    )

    result = await execute_process(p, **implicitly())

    if result.exit_code != 0:
        return FallibleImageBundle(
            None, exit_code=result.exit_code, stderr=result.stderr.decode(), stdout=result.stdout.decode()
        )

    # crane leaves the manifest unnamed and in its registry format; the rest of the backend
    # addresses an OCI manifest as `build:build`.
    index = await digest_subset_to_digest(DigestSubset(result.output_digest, PathGlobs(["build/index.json"])))
    index_content = (await get_digest_contents(index))[0].content
    replaced = ["build/index.json"]
    manifest = None
    try:
        descriptor = manifest_descriptor(index_content)
        if descriptor["mediaType"] == DOCKER_MANIFEST:
            manifest_path = blob_path(descriptor["digest"])
            manifest_blob = await digest_subset_to_digest(
                DigestSubset(result.output_digest, PathGlobs([manifest_path]))
            )
            manifest = docker_manifest_to_oci((await get_digest_contents(manifest_blob))[0].content)
            replaced.append(manifest_path)
        tagged_index = tag_layout_index(index_content, "build", manifest)
    except (KeyError, ValueError) as e:
        return FallibleImageBundle(None, exit_code=1, stderr=f"{e} ({image_ref})", stdout="")

    files = [FileContent("build/index.json", tagged_index)]
    if manifest is not None:
        files.append(FileContent(blob_path(manifest_descriptor(tagged_index)["digest"]), manifest))

    untouched = await digest_subset_to_digest(
        DigestSubset(result.output_digest, PathGlobs(["build/**", *(f"!{path}" for path in replaced)]))
    )
    rewritten = await create_digest(CreateDigest(files))
    output_digest = await merge_digests(MergeDigests([untouched, rewritten]))

    return FallibleImageBundle(
        ImageBundle(output_digest, f"sha256:{request.target.digest.value}", is_local=False)
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(FallibleImageBundleRequest, ImageBundlePullRequest),
    ]
