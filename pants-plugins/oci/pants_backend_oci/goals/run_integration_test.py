from __future__ import annotations

import os
from textwrap import dedent

import pytest
from pants.testutil.pants_integration_test import run_pants, setup_tmpdir

# The Docker runner is tested where Docker is, as in CI: `OCI_TEST_DOCKER=1`.
RUNNERS = pytest.mark.parametrize(
    "runner",
    [
        "podman",
        pytest.param(
            "docker",
            marks=pytest.mark.skipif(
                os.environ.get("OCI_TEST_DOCKER") != "1", reason="set OCI_TEST_DOCKER=1 to test Docker"
            ),
        ),
    ],
)


def runner_args(runner: str) -> list[str]:
    if runner == "podman":
        return []
    return ["--backend-packages=+['pants.backend.docker','pants_backend_oci.docker']", "--oci-runner=docker"]


@RUNNERS
def test_run_oci_container(runner: str) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent("""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d12",
                    repository="gcr.io/distroless/python3-debian12",
                    variants=dict(latest="87406cabca8cc6846b5ea08bd9b25960b4e29b845f76c1af0e3f4a0affd1d7b4"),
                    anonymous=True,
                )

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d12#latest"],
                    packages=[
                        ":example",
                    ],
                )
                """),
        "oci/example.py": dedent("""\
            print("Hello world!")
            """),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                *runner_args(runner),
                "--backend-packages=pants.backend.python",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                "run",
                f"{tmpdir}/oci:oci",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.11.*']"},
        )

    result.assert_success()
    assert result.stdout == "Hello world!\n"


@RUNNERS
def test_run_oci_container_file(runner: str) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent("""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d12",
                    repository="gcr.io/distroless/python3-debian12",
                    variants=dict(latest="87406cabca8cc6846b5ea08bd9b25960b4e29b845f76c1af0e3f4a0affd1d7b4"),
                    anonymous=True,
                )

                file(name="files", source="file.txt")

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d12#latest"],
                    packages=[
                        ":example",
                        ":files",
                    ],
                )
                """),
        "oci/file.txt": "Hello from a file!\n",
        "oci/resource.txt": "Hello from a file!\n",
        "oci/example.py": dedent("""\

            with open('/{tmpdir}/oci/file.txt', 'r') as f_:
                print(f_.read())
            """),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                *runner_args(runner),
                "--backend-packages=pants.backend.python",
                "--keep-sandboxes=on_failure",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                "run",
                f"{tmpdir}/oci:oci",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.11.*']"},
        )

    result.assert_success()
    assert result.stdout == "Hello from a file!\n\n"


@RUNNERS
def test_run_oci_container_files(runner: str) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent("""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d12",
                    repository="gcr.io/distroless/python3-debian12",
                    variants=dict(latest="87406cabca8cc6846b5ea08bd9b25960b4e29b845f76c1af0e3f4a0affd1d7b4"),
                    anonymous=True,
                )

                files(name="files", sources=["file.txt"])

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d12#latest"],
                    packages=[
                        ":example",
                        ":files",
                    ],
                )
                """),
        "oci/file.txt": "Hello from a file!\n",
        "oci/resource.txt": "Hello from a file!\n",
        "oci/example.py": dedent("""\

            with open('/{tmpdir}/oci/file.txt', 'r') as f_:
                print(f_.read())
            """),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                *runner_args(runner),
                "--backend-packages=pants.backend.python",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                "run",
                f"{tmpdir}/oci:oci",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.11.*']"},
        )

    result.assert_success()
    assert result.stdout == "Hello from a file!\n\n"
