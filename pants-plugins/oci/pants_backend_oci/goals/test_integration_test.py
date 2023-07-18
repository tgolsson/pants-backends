from __future__ import annotations

import os
from textwrap import dedent

from pants.testutil.pants_integration_test import run_pants, setup_tmpdir
from pants.version import PANTS_SEMVER, Version

VERSION = "215"
if PANTS_SEMVER > Version("2.17.0dev0"):
    VERSION = "217"
elif PANTS_SEMVER > Version("2.16.0dev0"):
    VERSION = "216"

SIMPLE_EXPECTED_DIGEST = {
    "215": "e90e0558571ea8aebe5546e951fef0a5319063e3c8e8006a1bf894cc14e845b",
    "216": "918e005db3ee24c6cc3b085f8546c04a353ace77e6d372069c85480ac4a39135",
    "217": "bcb2ec2e770439e7f46bced23cb77bbd54cd8946a18fc7ba9108d703506d155",
}[VERSION]


def test_test_oci_container() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "../oci/BUILD": dedent(
            f"""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d11",
                    repository="gcr.io/distroless/python3-debian11",
                    variants=dict(latest="62da909329b74929181b2eac28da3be52b816c7d3d3f676bda04887c98c41593"),
                )

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d11#latest"],
                    packages=[
                        ":example",
                    ],
                )

                oci_structure_test(
                    name="oci-test",
                    base=[":oci"],
                    expected_digest="{SIMPLE_EXPECTED_DIGEST}",
                )
            """
        ),
        "../oci/example.py": dedent(
            """\
            print("Hello world!")
            """
        ),
    }

    uid = os.getuid()
    gid = os.getgid()

    with setup_tmpdir(build_inputs) as _:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "--backend-packages=pants.backend.python",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                f"--oci-uid-map=['0:{uid}:1']",
                "--oci-uid-map=1:100000:65536",
                f"--oci-gid-map=['0:{gid}:1']",
                "--oci-gid-map=1:100000:65536",
                "--keep-sandboxes=always",
                "test",
                "oci:oci-test",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()


FILE_EXPECTED_DIGEST = {
    "215": "405fb07fc971eb02248e0488226cc7ada6282dfd6392ec52fbe2a9915815dfc",
    "216": "82abf78018f3360c45fae1edb0c7ecb1fd57b9daf87f65d61904d8594123a8a",
    "217": "f42ef761779b7f9bab3ae787d54a8c31f0a9446aa3e7bd6f622ddc0bdfd407a5",
}[VERSION]


def test_test_oci_container_file() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "../oci/BUILD": dedent(
            f"""\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d11",
                    repository="gcr.io/distroless/python3-debian11",
                    variants=dict(latest="62da909329b74929181b2eac28da3be52b816c7d3d3f676bda04887c98c41593"),
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

                oci_structure_test(
                    name="oci-test",
                    base=[":oci"],
                    expected_digest="{FILE_EXPECTED_DIGEST}",
                )
                """
        ),
        "../oci/file.txt": "Hello from a file!\n",
        "../oci/resource.txt": "Hello from a file!\n",
        "../oci/example.py": dedent(
            """
            print("Hello world!")
            """
        ),
    }

    uid = os.getuid()
    gid = os.getgid()

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "--backend-packages=pants.backend.python",
                "--keep-sandboxes=always",
                "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
                f"--oci-uid-map=['0:{uid}:1']",
                "--oci-uid-map=1:100000:65536",
                f"--oci-gid-map=['0:{gid}:1']",
                "--oci-gid-map=1:100000:65536",
                "test",
                "oci:oci-test",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()
