import json
from types import SimpleNamespace

import pytest
from pants.backend.docker.util_rules.docker_binary import DockerBinary
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.process import Process
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.docker.rules import (
    DockerBuildRequest,
    DockerRunRequest,
    docker_build,
    docker_run,
)
from pants_backend_oci.util_rules import docker_builder
from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.sandboxed import OciBuilder

BUNDLE = ImageBundle(EMPTY_DIGEST, "sha256:abc", True)
DOCKER = DockerBinary("/usr/bin/docker", "fingerprint", extra_env={"PATH": "__tools/bin"})
DOCKER_ENV = {"DOCKER_HOST": "unix:///run/docker.sock"}


def env_vars_subset(request, **kwargs):
    assert list(request.requested) == ["DOCKER_HOST"]
    return DOCKER_ENV


@pytest.mark.parametrize("interactive,tty", [(False, False), (False, True), (True, True)])
def test_run_loads_the_archive_and_runs_it_like_the_docker_backend(interactive, tty):
    archive_requests = []

    def archive(request, **kwargs):
        archive_requests.append(request)
        return Process(("export",), description="archive", output_files=("image.tar",))

    process = run_rule_with_mocks(
        docker_run,
        rule_args=[
            DockerRunRequest(BUNDLE, Address("image"), interactive, tty),
            DOCKER,
            SimpleNamespace(run_args=("-p", "8080:80")),
            SimpleNamespace(env_vars=("DOCKER_HOST",)),
            OciBuilder(EMPTY_DIGEST),
        ],
        mock_calls={
            "pants_backend_oci.goals.package.package_oci_archive": archive,
            "pants.engine.process.fallible_to_exec_result_or_raise": lambda *a, **k: SimpleNamespace(
                output_digest=EMPTY_DIGEST
            ),
            "pants.core.util_rules.env_vars.environment_vars_subset": env_vars_subset,
        },
    )
    (request,) = archive_requests
    assert (request.output_filename, request.name) == ("image.tar", "pants-abc:latest")
    assert process.argv[:2] == ("{chroot}/" + OciBuilder.exe, "docker_runner")
    assert process.argv[-1] == "--"
    assert process.argv[process.argv.index("--archive") + 1] == "{chroot}/image.tar"
    run = json.loads(process.argv[process.argv.index("--run") + 1])
    flags = ["--rm"] + (["--interactive"] if interactive else []) + (["--tty"] if tty else [])
    assert run == ["/usr/bin/docker", "run", *flags, "-p", "8080:80", "pants-abc:latest"]
    assert process.env["DOCKER_HOST"] == "unix:///run/docker.sock"
    # `[docker].tools` are in the sandbox the run starts from.
    assert process.env["PATH"] == "{chroot}/__tools/bin"


def test_build_runs_the_builder_with_the_docker_backends_environment():
    processes = []
    run_rule_with_mocks(
        docker_build,
        rule_args=[
            DockerBuildRequest(BUNDLE, ("layer",), "build", output_files=("layer.tar",)),
            DOCKER,
            SimpleNamespace(env_vars=("DOCKER_HOST",)),
            OciBuilder(EMPTY_DIGEST),
            SimpleNamespace(system_binary_paths=("/usr/bin", "/bin")),
        ],
        mock_calls={
            "pants.core.util_rules.env_vars.environment_vars_subset": env_vars_subset,
            "pants.engine.process.fallible_to_exec_result_or_raise": processes.append,
        },
    )
    (process,) = processes
    assert process.argv[1:] == ("docker_builder", "--docker", "/usr/bin/docker", "layer")
    assert process.env["DOCKER_HOST"] == "unix:///run/docker.sock"
    assert process.env["PATH"] == "__tools/bin:/usr/bin:/bin"
    assert process.env["__OCI_DOCKER_FINGERPRINT"] == "fingerprint"
    assert process.output_files == ("layer.tar",)


def test_build_passes_a_configured_home_under_another_name():
    processes = []

    def env_with_home(request, **kwargs):
        return {"HOME": "/configured/home"}

    run_rule_with_mocks(
        docker_build,
        rule_args=[
            DockerBuildRequest(BUNDLE, ("layer",), "build"),
            DOCKER,
            SimpleNamespace(env_vars=("HOME",)),
            OciBuilder(EMPTY_DIGEST),
            SimpleNamespace(system_binary_paths=("/usr/bin",)),
        ],
        mock_calls={
            "pants.core.util_rules.env_vars.environment_vars_subset": env_with_home,
            "pants.engine.process.fallible_to_exec_result_or_raise": processes.append,
        },
    )
    (process,) = processes
    assert process.env[docker_builder.DOCKER_HOME] == "/configured/home"
    # The builder's own HOME stays the scie's cache.
    assert process.env["HOME"] == OciBuilder(EMPTY_DIGEST).home
