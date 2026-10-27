import json
from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.env_vars import EnvironmentVars
from pants.engine.fs import EMPTY_DIGEST, Digest
from pants.engine.platform import Platform
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.targets import PullImage
from pants_backend_oci.util_rules.pull_image_bundle import (
    ImageBundlePullFieldSet,
    ImageBundlePullRequest,
    pull_oci_image,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder

DIGEST = "42ff8b00a03517f39a968d2e2a6e82c6445586c95e484ff079cbf06f7590cfa7"


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
            OciBuilder(EMPTY_DIGEST),
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


def run_pull(results: list, processes: list, subsets: list | None = None):
    subsets = [] if subsets is None else subsets
    target = PullImage(
        {"repository": "registry.k8s.io/pause", "digest": DIGEST, "anonymous": True},
        Address("images", target_name="pause"),
    )

    def execute(process, **kwargs):
        processes.append(process)
        return results[len(processes) - 1]

    return run_rule_with_mocks(
        pull_oci_image,
        rule_args=[
            ImageBundlePullRequest(ImageBundlePullFieldSet.create(target)),
            SimpleNamespace(get_request=lambda _: None),
            Platform.linux_x86_64,
            OciBuilder(BUILDER),
        ],
        mock_calls={
            "pants.core.util_rules.external_tool.download_external_tool": lambda request: SimpleNamespace(
                exe="./crane", digest=EMPTY_DIGEST
            ),
            "pants.engine.intrinsics.execute_process": execute,
            "pants.engine.intrinsics.digest_subset_to_digest": lambda subset: subsets.append(subset)
            or METADATA,
        },
    )


PULLED = Digest("1" * 64, 1)
BUILDER = Digest("2" * 64, 2)
NORMALIZED = Digest("4" * 64, 4)
METADATA = Digest("5" * 64, 5)


def test_pull_normalizes_the_layout_in_a_sandbox() -> None:
    processes: list = []
    subsets: list = []
    described = {"digest": "sha256:m", "metadata": ["sha256:m", "sha256:c"], "blobs": ["sha256:l"]}

    result = run_pull(
        [
            SimpleNamespace(exit_code=0, output_digest=PULLED),
            SimpleNamespace(
                exit_code=0, output_digest=NORMALIZED, stdout=json.dumps(described).encode(), stderr=b""
            ),
        ],
        processes,
        subsets,
    )

    assert result.exit_code == 0
    assert result.output.digest == NORMALIZED
    assert result.output.metadata == METADATA
    (subset,) = subsets
    assert subset.digest == NORMALIZED
    assert set(subset.globs.globs) == {
        "build/index.json",
        "build/oci-layout",
        "build/blobs/sha256/m",
        "build/blobs/sha256/c",
    }
    assert result.output.image_sha == f"sha256:{DIGEST}"
    assert not result.output.is_local
    _, normalize = processes
    assert normalize.argv[:3] == (OciBuilder.exe, "oci_layout_builder", "normalize")
    assert normalize.input_digest == PULLED
    assert normalize.immutable_input_digests == {"__oci_builder": BUILDER}
    assert normalize.output_directories == ("build",)


def test_pull_reports_normalization_errors() -> None:
    result = run_pull(
        [
            SimpleNamespace(exit_code=0, output_digest=PULLED),
            SimpleNamespace(exit_code=1, stdout=b"", stderr=b"error: Cannot convert media type 'x' to OCI\n"),
        ],
        [],
    )

    assert result.output is None
    assert result.exit_code == 1
    assert result.stderr == f"Cannot convert media type 'x' to OCI (registry.k8s.io/pause@sha256:{DIGEST})"
