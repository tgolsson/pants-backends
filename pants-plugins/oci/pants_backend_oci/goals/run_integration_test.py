from __future__ import annotations

import os
from textwrap import dedent

from pants.testutil.pants_integration_test import run_pants, setup_tmpdir


def test_run_oci_container() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent("""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d11",
                    repository="gcr.io/distroless/python3-debian11",
                    variants=dict(latest="62da909329b74929181b2eac28da3be52b816c7d3d3f676bda04887c98c41593"),
                    anonymous=True,
                )

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d11#latest"],
                    packages=[
                        ":example",
                    ],
                )
                """),
        "oci/example.py": dedent("""\
            print("Hello world!")
            """),
    }

    uid = os.getuid()
    gid = os.getgid()

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "--backend-packages=pants.backend.python",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                f"--oci-uid-map=['0:{uid}:1']",
                "--oci-uid-map=1:100000:65536",
                f"--oci-gid-map=['0:{gid}:1']",
                "--oci-gid-map=1:100000:65536",
                "run",
                f"{tmpdir}/oci:oci",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()
    assert result.stdout == "Hello world!\n"


def test_run_oci_container_file() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent("""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d11",
                    repository="gcr.io/distroless/python3-debian11",
                    variants=dict(latest="62da909329b74929181b2eac28da3be52b816c7d3d3f676bda04887c98c41593"),
                    anonymous=True,
                )

                file(name="files", source="file.txt")

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d11#latest"],
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

    uid = os.getuid()
    gid = os.getgid()

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "--backend-packages=pants.backend.python",
                "--keep-sandboxes=on_failure",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                f"--oci-uid-map=['0:{uid}:1']",
                "--oci-uid-map=1:100000:65536",
                f"--oci-gid-map=['0:{gid}:1']",
                "--oci-gid-map=1:100000:65536",
                "run",
                f"{tmpdir}/oci:oci",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()
    assert result.stdout == "Hello from a file!\n\n"


def test_run_oci_container_files() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent("""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d11",
                    repository="gcr.io/distroless/python3-debian11",
                    variants=dict(latest="62da909329b74929181b2eac28da3be52b816c7d3d3f676bda04887c98c41593"),
                    anonymous=True,
                )

                files(name="files", sources=["file.txt"])

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d11#latest"],
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

    uid = os.getuid()
    gid = os.getgid()

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "--backend-packages=pants.backend.python",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                f"--oci-uid-map=['0:{uid}:1']",
                "--oci-uid-map=1:100000:65536",
                f"--oci-gid-map=['0:{gid}:1']",
                "--oci-gid-map=1:100000:65536",
                "run",
                f"{tmpdir}/oci:oci",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()
    assert result.stdout == "Hello from a file!\n\n"
