from pathlib import PurePath
from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.goals.run import (
    PodmanBinary,
    RunImageBundleProcessRequest,
    find_podman,
    prepare_run_image_bundle,
)
from pants_backend_oci.targets import ImageBuild
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle


@pytest.mark.parametrize("interactive,terminal", [(False, False), (False, True), (True, False)])
def test_prepare_archives_image_before_launch(interactive, terminal):
    target = ImageBuild({"terminal": terminal}, Address("image"))
    image = FallibleImageBundle(ImageBundle(EMPTY_DIGEST, "sha256:abc", True))
    tar_requests = []
    executions = []

    def tar(request, **kwargs):
        tar_requests.append(request)
        return Process(("tar", "-cf", "image.tar"), description="archive", output_files=("image.tar",))

    def execute(process, **kwargs):
        executions.append(process)
        return SimpleNamespace(output_digest=EMPTY_DIGEST)

    result = run_rule_with_mocks(
        prepare_run_image_bundle,
        rule_args=[
            RunImageBundleProcessRequest(target, interactive),
            PodmanBinary("/bin/podman", "abc"),
            SimpleNamespace(path="/python"),
            SimpleNamespace(val=PurePath("/caches")),
            Platform.linux_x86_64,
        ],
        mock_calls={
            "pants.engine.internals.graph.resolve_target": lambda *args, **kwargs: SimpleNamespace(
                target=target
            ),
            "pants_backend_oci.util_rules.image_bundle.ibr_to_fibr": lambda *args, **kwargs: SimpleNamespace(
                request=object()
            ),
            "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda *args, **kwargs: image,
            "pants_backend_oci.util_rules.archive.tar_directory_process": tar,
            "pants.engine.intrinsics.merge_digests": lambda *args: EMPTY_DIGEST,
            "pants.engine.intrinsics.create_digest": lambda *args: EMPTY_DIGEST,
            "pants.engine.process.fallible_to_exec_result_or_raise": execute,
        },
    )
    assert len(executions) == 1
    assert tar_requests[0].directory == "build"
    assert not tar_requests[0].gzip
    assert result.argv[-1] == "--"
    assert ("--interactive" in result.argv) == interactive
    assert ("--tty" in result.argv) == (interactive or terminal)
    assert "/caches/oci_podman/v1" in result.argv


def test_missing_podman_error():
    def missing(request, **kwargs):
        from pants.core.util_rules.system_binaries import BinaryPaths

        return BinaryPaths(binary_name="podman", paths=())

    with pytest.raises(Exception, match="Install podman: https://podman.io/docs/installation"):
        run_rule_with_mocks(
            find_podman,
            rule_args=[SimpleNamespace(system_binary_paths=("/empty",)), Platform.linux_x86_64],
            mock_calls={"pants.core.util_rules.system_binaries.find_binary": missing},
        )


def test_macos_rejected():
    with pytest.raises(ValueError, match="podman machine is not supported"):
        run_rule_with_mocks(find_podman, rule_args=[SimpleNamespace(), Platform.macos_arm64])


@pytest.mark.parametrize("exit_code,dependency_failed", [(1, False), (0, True)])
def test_failed_image_does_not_start_archive(exit_code, dependency_failed):
    target = ImageBuild({}, Address("image"))
    with pytest.raises(ValueError, match="broken image"):
        run_rule_with_mocks(
            prepare_run_image_bundle,
            rule_args=[
                RunImageBundleProcessRequest(target),
                PodmanBinary("/podman", "abc"),
                SimpleNamespace(path="/python"),
                SimpleNamespace(val=PurePath("/caches")),
                Platform.linux_x86_64,
            ],
            mock_calls={
                "pants.engine.internals.graph.resolve_target": lambda *args, **kwargs: SimpleNamespace(
                    target=target
                ),
                "pants_backend_oci.util_rules.image_bundle.ibr_to_fibr": lambda *args,
                **kwargs: SimpleNamespace(request=object()),
                "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda *args,
                **kwargs: FallibleImageBundle(
                    None, exit_code=exit_code, dependency_failed=dependency_failed, stderr="broken image"
                ),
            },
        )
