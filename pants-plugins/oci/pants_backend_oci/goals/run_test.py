from pathlib import PurePath
from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.goals.run import RunImageBundleProcessRequest, prepare_run_image_bundle
from pants_backend_oci.targets import ImageBuild
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.run import PodmanBinary, find_podman
from pants_backend_oci.util_rules.sandboxed import OciBuilder


@pytest.mark.parametrize("interactive,terminal", [(False, False), (False, True), (True, False)])
def test_prepare_archives_image_before_launch(interactive, terminal):
    target = ImageBuild({"terminal": terminal}, Address("image"))
    image = FallibleImageBundle(ImageBundle(EMPTY_DIGEST, "sha256:abc", True))
    archive_requests = []
    executions = []

    def archive(request, **kwargs):
        archive_requests.append(request)
        return Process(("python", "export", "image.tar"), description="archive", output_files=("image.tar",))

    def execute(process, **kwargs):
        executions.append(process)
        return SimpleNamespace(output_digest=EMPTY_DIGEST)

    result = run_rule_with_mocks(
        prepare_run_image_bundle,
        rule_args=[
            RunImageBundleProcessRequest(target, interactive),
            PodmanBinary("/bin/podman", "abc"),
            OciBuilder(EMPTY_DIGEST),
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
            "pants_backend_oci.goals.package.package_oci_archive": archive,
            "pants.engine.process.fallible_to_exec_result_or_raise": execute,
        },
    )
    assert len(executions) == 1
    assert archive_requests[0].input_digest == image.output.digest
    assert archive_requests[0].output_filename == "image.tar"
    assert result.argv[-1] == "--"
    assert ("--interactive" in result.argv) == interactive
    assert ("--tty" in result.argv) == (interactive or terminal)
    assert "/caches/oci_podman/v2" in result.argv
    assert result.argv[:2] == ("{chroot}/" + OciBuilder.exe, "podman_runner")


def test_missing_podman_error():
    def missing(request, **kwargs):
        from pants.core.util_rules.system_binaries import BinaryPaths

        return BinaryPaths(binary_name="podman", paths=())

    with pytest.raises(Exception, match="Install podman: https://podman.io/docs/installation"):
        run_rule_with_mocks(
            find_podman,
            rule_args=[SimpleNamespace(system_binary_paths=("/empty",)), Platform.linux_x86_64, None],
            mock_calls={"pants.core.util_rules.system_binaries.find_binary": missing},
        )


def test_macos_rejected():
    with pytest.raises(ValueError, match="podman machine is not supported"):
        run_rule_with_mocks(find_podman, rule_args=[SimpleNamespace(), Platform.macos_arm64, None])


@pytest.mark.parametrize("exit_code,dependency_failed", [(1, False), (0, True)])
def test_failed_image_does_not_start_archive(exit_code, dependency_failed):
    target = ImageBuild({}, Address("image"))
    with pytest.raises(ValueError, match="broken image"):
        run_rule_with_mocks(
            prepare_run_image_bundle,
            rule_args=[
                RunImageBundleProcessRequest(target),
                PodmanBinary("/podman", "abc"),
                OciBuilder(EMPTY_DIGEST),
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
