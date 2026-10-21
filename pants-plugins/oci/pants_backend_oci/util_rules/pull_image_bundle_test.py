import hashlib
import json
from fnmatch import fnmatch
from itertools import permutations
from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.env_vars import EnvironmentVars
from pants.engine.fs import (
    EMPTY_DIGEST,
    CreateDigest,
    Digest,
    DigestSubset,
    FileContent,
    MergeDigests,
)
from pants.engine.platform import Platform
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.targets import PullImage
from pants_backend_oci.util_rules.pull_image_bundle import (
    ImageBundlePullFieldSet,
    ImageBundlePullRequest,
    docker_manifest_to_oci,
    pull_oci_image,
    tag_layout_index,
)

DIGEST = "42ff8b00a03517f39a968d2e2a6e82c6445586c95e484ff079cbf06f7590cfa7"


def test_tag_layout_index_names_the_manifest() -> None:
    index = {"schemaVersion": 2, "manifests": [{"digest": "sha256:abc", "annotations": {"keep": "me"}}]}

    tagged = json.loads(tag_layout_index(json.dumps(index).encode(), "build"))

    assert tagged["manifests"][0]["annotations"] == {
        "keep": "me",
        "org.opencontainers.image.ref.name": "build",
    }


def test_tag_layout_index_points_at_replacement_manifest() -> None:
    index = {"manifests": [{"mediaType": "application/vnd.docker.distribution.manifest.v2+json"}]}
    manifest = b'{"mediaType":"application/vnd.oci.image.manifest.v1+json"}'

    (descriptor,) = json.loads(tag_layout_index(json.dumps(index).encode(), "build", manifest))["manifests"]

    assert descriptor == {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": f"sha256:{hashlib.sha256(manifest).hexdigest()}",
        "size": len(manifest),
        "annotations": {"org.opencontainers.image.ref.name": "build"},
    }


def test_docker_manifest_to_oci_translates_media_types() -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": "sha256:c"},
        "layers": [
            {"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "digest": "sha256:l"},
            {"mediaType": "application/vnd.oci.image.layer.v1.tar+zstd", "digest": "sha256:z"},
        ],
    }

    converted = json.loads(docker_manifest_to_oci(json.dumps(manifest).encode()))

    assert converted["mediaType"] == "application/vnd.oci.image.manifest.v1+json"
    assert converted["config"] == {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": "sha256:c",
    }
    assert [layer["mediaType"] for layer in converted["layers"]] == [
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
    ]


def test_docker_manifest_to_oci_rejects_unknown_media_types() -> None:
    manifest = {
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.plugin.v1+json"},
        "layers": [],
    }

    with pytest.raises(ValueError, match="plugin"):
        docker_manifest_to_oci(json.dumps(manifest).encode())


@pytest.mark.parametrize("count", [0, 2])
def test_tag_layout_index_requires_one_manifest(count) -> None:
    index = {"schemaVersion": 2, "manifests": [{"digest": f"sha256:{i}"} for i in range(count)]}

    with pytest.raises(ValueError, match=f"found {count}"):
        tag_layout_index(json.dumps(index).encode(), "build")


@pytest.mark.parametrize(
    "fields, platform, expected_platform",
    [
        ({}, Platform.linux_x86_64, "linux/amd64"),
        ({}, Platform.macos_arm64, "linux/arm64"),
        ({"architecture": "arm64"}, Platform.linux_x86_64, "linux/arm64"),
        ({"os": "windows", "architecture": "amd64"}, Platform.linux_arm64, "windows/amd64"),
    ],
)
@pytest.mark.parametrize("anonymous", [True, False])
def test_pull_runs_crane(fields, platform, expected_platform, anonymous) -> None:
    target = PullImage(
        {"repository": "gcr.io/distroless/static", "digest": DIGEST, "anonymous": anonymous, **fields},
        Address("images", target_name="base"),
    )
    processes = []
    credentials = EnvironmentVars({"HOME": "/home/user"})

    def execute(process, **kwargs):
        processes.append(process)
        return SimpleNamespace(exit_code=1, stderr=b"no network", stdout=b"")

    result = run_rule_with_mocks(
        pull_oci_image,
        rule_args=[
            ImageBundlePullRequest(ImageBundlePullFieldSet.create(target)),
            SimpleNamespace(get_request=lambda _: None),
            platform,
        ],
        mock_calls={
            "pants.core.util_rules.external_tool.download_external_tool": lambda request: SimpleNamespace(
                exe="./crane", digest=EMPTY_DIGEST
            ),
            "pants.core.util_rules.env_vars.environment_vars_subset": lambda request, **kwargs: credentials,
            "pants.engine.intrinsics.execute_process": execute,
        },
    )

    assert result.exit_code == 1
    assert result.stderr == "no network"
    (process,) = processes
    assert process.argv == (
        "./crane",
        "pull",
        "--format=oci",
        "--platform",
        expected_platform,
        f"gcr.io/distroless/static@sha256:{DIGEST}",
        "build",
    )
    assert dict(process.env) == ({} if anonymous else {"HOME": "/home/user"})


class FakeStore:
    """Stand-in for the engine's digest intrinsics, holding each digest as a dict of files."""

    def __init__(self) -> None:
        self.trees: dict[Digest, dict[str, bytes]] = {}

    def put(self, files: dict[str, bytes]) -> Digest:
        digest = Digest(hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest(), len(files))
        self.trees[digest] = files
        return digest

    def subset(self, subset: DigestSubset) -> Digest:
        include = [glob for glob in subset.globs.globs if not glob.startswith("!")]
        exclude = {glob[1:] for glob in subset.globs.globs if glob.startswith("!")}
        files = {
            path: content
            for path, content in self.trees[subset.digest].items()
            if path not in exclude and any(path == glob or fnmatch(path, glob) for glob in include)
        }
        return self.put(files)

    def contents(self, digest: Digest) -> list[FileContent]:
        return [FileContent(path, content) for path, content in self.trees[digest].items()]

    def create(self, request: CreateDigest) -> Digest:
        return self.put({file.path: file.content for file in request})

    def merge(self, request: MergeDigests) -> Digest:
        # MergeDigests doesn't expose its inputs, so find the pair it was built from.
        (digests,) = [pair for pair in permutations(self.trees, 2) if MergeDigests(pair) == request]
        merged: dict[str, bytes] = {}
        for digest in digests:
            for path, content in self.trees[digest].items():
                assert merged.setdefault(path, content) == content, f"conflicting {path}"
        return self.put(merged)


def test_pull_converts_docker_manifest_to_tagged_oci_layout() -> None:
    target = PullImage(
        {"repository": "registry.k8s.io/pause", "digest": DIGEST, "anonymous": True},
        Address("images", target_name="pause"),
    )
    docker_manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": "sha256:c"},
            "layers": [
                {"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "digest": "sha256:l"}
            ],
        }
    ).encode()
    docker_digest = hashlib.sha256(docker_manifest).hexdigest()
    index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                "artifactType": "application/vnd.docker.container.image.v1+json",
                "digest": f"sha256:{docker_digest}",
                "size": len(docker_manifest),
            }
        ],
    }
    store = FakeStore()
    pulled = store.put(
        {
            "build/oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
            "build/index.json": json.dumps(index).encode(),
            f"build/blobs/sha256/{docker_digest}": docker_manifest,
            "build/blobs/sha256/c": b"config",
            "build/blobs/sha256/l": b"layer",
        }
    )

    result = run_rule_with_mocks(
        pull_oci_image,
        rule_args=[
            ImageBundlePullRequest(ImageBundlePullFieldSet.create(target)),
            SimpleNamespace(get_request=lambda _: None),
            Platform.linux_x86_64,
        ],
        mock_calls={
            "pants.core.util_rules.external_tool.download_external_tool": lambda request: SimpleNamespace(
                exe="./crane", digest=EMPTY_DIGEST
            ),
            "pants.engine.intrinsics.execute_process": lambda process, **kwargs: SimpleNamespace(
                exit_code=0, output_digest=pulled
            ),
            "pants.engine.intrinsics.digest_subset_to_digest": store.subset,
            "pants.engine.intrinsics.get_digest_contents": store.contents,
            "pants.engine.intrinsics.create_digest": store.create,
            "pants.engine.intrinsics.merge_digests": store.merge,
        },
    )

    assert result.exit_code == 0
    assert result.output.image_sha == f"sha256:{DIGEST}"
    layout = store.trees[result.output.digest]
    (descriptor,) = json.loads(layout["build/index.json"])["manifests"]
    oci_manifest = layout[f"build/blobs/sha256/{descriptor['digest'].removeprefix('sha256:')}"]
    assert descriptor == {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": "application/vnd.oci.image.config.v1+json",
        "digest": f"sha256:{hashlib.sha256(oci_manifest).hexdigest()}",
        "size": len(oci_manifest),
        "annotations": {"org.opencontainers.image.ref.name": "build"},
    }
    assert json.loads(oci_manifest)["layers"][0]["mediaType"] == "application/vnd.oci.image.layer.v1.tar+gzip"
    assert f"build/blobs/sha256/{docker_digest}" not in layout
    assert {"build/oci-layout", "build/blobs/sha256/c", "build/blobs/sha256/l"} <= layout.keys()
