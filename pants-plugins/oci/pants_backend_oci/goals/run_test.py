from pathlib import PurePath
from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST, Digest
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.unions import UnionMembership, UnionRule
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.goals.run import (
    PodmanRunRequest,
    RunImageBundleProcessRequest,
    podman_run,
    prepare_run_image_bundle,
)
from pants_backend_oci.subsystem import OciRunner
from pants_backend_oci.targets import ImageBuild
from pants_backend_oci.util_rules.image_bundle import FallibleImageBundle, ImageBundle
from pants_backend_oci.util_rules.run import PodmanBinary, find_podman
from pants_backend_oci.util_rules.runner import ContainerRunRequest
from pants_backend_oci.util_rules.sandboxed import OciBuilder


@pytest.mark.parametrize("interactive,terminal", [(False, False), (False, True), (True, False)])
def test_prepare_runs_the_image_with_the_configured_runner(interactive, terminal):
    target = ImageBuild({"terminal": terminal}, Address("image"))
    image = FallibleImageBundle(ImageBundle(EMPTY_DIGEST, "sha256:abc", True, metadata=EMPTY_DIGEST))
    runs = []

    def run(request, **kwargs):
        runs.append(request)
        return Process(("run",), description="run")

    result = run_rule_with_mocks(
        prepare_run_image_bundle,
        rule_args=[
            RunImageBundleProcessRequest(target, interactive),
            SimpleNamespace(runner=OciRunner.podman),
            UnionMembership.from_rules([UnionRule(ContainerRunRequest, PodmanRunRequest)]),
        ],
        mock_calls={
            "pants.engine.internals.graph.resolve_target": lambda *args, **kwargs: SimpleNamespace(
                target=target
            ),
            "pants_backend_oci.util_rules.image_bundle.ibr_to_fibr": lambda *args, **kwargs: SimpleNamespace(
                request=object()
            ),
            "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda *args, **kwargs: image,
            "pants_backend_oci.util_rules.runner.container_run": run,
        },
    )
    assert result.argv == ("run",)
    assert runs == [PodmanRunRequest(image.output, Address("image"), interactive, interactive or terminal)]


def test_docker_runner_needs_its_backends():
    target = ImageBuild({}, Address("image"))
    image = FallibleImageBundle(ImageBundle(EMPTY_DIGEST, "sha256:abc", True, metadata=EMPTY_DIGEST))
    with pytest.raises(ValueError, match="needs the `pants.backend.docker` and `pants_backend_oci.docker`"):
        run_rule_with_mocks(
            prepare_run_image_bundle,
            rule_args=[
                RunImageBundleProcessRequest(target),
                SimpleNamespace(runner=OciRunner.docker),
                UnionMembership.from_rules([UnionRule(ContainerRunRequest, PodmanRunRequest)]),
            ],
            mock_calls={
                "pants.engine.internals.graph.resolve_target": lambda *args, **kwargs: SimpleNamespace(
                    target=target
                ),
                "pants_backend_oci.util_rules.image_bundle.ibr_to_fibr": lambda *args,
                **kwargs: SimpleNamespace(request=object()),
                "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda *args,
                **kwargs: image,
            },
        )


def podman_run_process(request, platform=Platform.linux_x86_64, connection=""):
    archive_requests = []
    executions = []

    def archive(request, **kwargs):
        archive_requests.append(request)
        return Process(("python", "export", "image.tar"), description="archive", output_files=("image.tar",))

    def execute(process, **kwargs):
        executions.append(process)
        return SimpleNamespace(output_digest=EMPTY_DIGEST)

    result = run_rule_with_mocks(
        podman_run,
        rule_args=[
            request,
            PodmanBinary("/bin/podman"),
            OciBuilder(EMPTY_DIGEST),
            SimpleNamespace(val=PurePath("/caches")),
            platform,
            SimpleNamespace(podman_connection=connection),
        ],
        mock_calls={
            "pants_backend_oci.goals.package.package_oci_archive": archive,
            "pants.engine.process.fallible_to_exec_result_or_raise": execute,
        },
    )
    assert len(executions) == len(archive_requests)
    return result, archive_requests


@pytest.mark.parametrize("interactive,tty", [(False, False), (False, True), (True, True)])
def test_local_podman_reads_the_layout_without_an_archive(interactive, tty):
    layout = Digest("a" * 64, 1)
    bundle = ImageBundle(layout, "sha256:abc", True, metadata=EMPTY_DIGEST)
    result, archives = podman_run_process(PodmanRunRequest(bundle, Address("image"), interactive, tty))
    # Capturing an archive of a multi-GB image costs more than running it.
    assert archives == []
    assert result.input_digest == EMPTY_DIGEST
    assert result.immutable_input_digests["__oci_image"] == layout
    argv = list(result.argv)
    assert argv[argv.index("--layout") + 1] == "{chroot}/__oci_image/build"
    assert argv[argv.index("--tag") + 1] == "build"
    assert "--archive" not in argv
    assert "--connection" not in argv and "--digest" not in argv
    assert result.argv[-1] == "--"
    assert ("--interactive" in result.argv) == interactive
    assert ("--tty" in result.argv) == tty
    assert "/caches/oci_podman/v2" in result.argv
    assert result.argv[4:6] == (OciBuilder.exe, "podman_runner")


@pytest.mark.parametrize(
    "platform,connection",
    [
        (Platform.macos_arm64, ""),
        (Platform.macos_x86_64, ""),
        (Platform.linux_x86_64, "ci"),
        (Platform.macos_arm64, "vm"),
    ],
)
def test_podman_runs_through_a_connection_off_linux_or_when_named(platform, connection):
    bundle = ImageBundle(Digest("a" * 64, 1), "sha256:abc", True, metadata=EMPTY_DIGEST)
    result, archives = podman_run_process(
        PodmanRunRequest(bundle, Address("image"), True, True), platform, connection
    )
    # The archive names the image, so loading it leaves no unnamed image behind.
    assert [archive.name for archive in archives] == ["localhost/pants-oci-run:abc"]
    assert archives[0].input_digest == bundle.digest
    argv = list(result.argv)
    assert argv[argv.index("--archive") + 1] == "{chroot}/image.tar"
    assert "--layout" not in argv
    assert argv[argv.index("--connection") + 1] == connection
    assert argv[argv.index("--digest") + 1] == "sha256:abc"
    assert argv[-3:] == ["--interactive", "--tty", "--"]


def test_missing_podman_error():
    def missing(request, **kwargs):
        from pants.core.util_rules.system_binaries import BinaryPaths

        return BinaryPaths(binary_name="podman", paths=())

    with pytest.raises(Exception, match="Install podman: https://podman.io/docs/installation"):
        run_rule_with_mocks(
            find_podman,
            rule_args=[SimpleNamespace(system_binary_paths=("/empty",))],
            mock_calls={"pants.core.util_rules.system_binaries.find_binary": missing},
        )


@pytest.mark.parametrize("exit_code,dependency_failed", [(1, False), (0, True)])
def test_failed_image_does_not_start_runner(exit_code, dependency_failed):
    target = ImageBuild({}, Address("image"))
    with pytest.raises(ValueError, match="broken image"):
        run_rule_with_mocks(
            prepare_run_image_bundle,
            rule_args=[
                RunImageBundleProcessRequest(target),
                SimpleNamespace(runner=OciRunner.podman),
                UnionMembership.from_rules([UnionRule(ContainerRunRequest, PodmanRunRequest)]),
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
