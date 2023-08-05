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
    "216": "cb0e4a9d23544283b90cfbaf976d7384a6397216973cb9b0a6c79538409ffa1b",
    "217": "1b2bbbd6a2cd86d45ed94daa6403fa418d48967383d8f688ec41406d25781b2",
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

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python", layout="packed")

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
                "--no-local-cache",
                "--keep-sandboxes=always",
                "test",
                "oci:oci-test",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()


FILE_EXPECTED_DIGEST = {
    "215": "405fb07fc971eb02248e0488226cc7ada6282dfd6392ec52fbe2a9915815dfc",
    "216": "03733c67e62860782d5bd7312f1fd2c0edab2b2ad64b5cc16f0eeb92e09c4eac",
    "217": "3a9f9b854c3d676f00167102b8eb6f8929ea3240cf8fe36bed3d1a73ace9d21c",
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

                pex_binary(name="example", entry_point="example.py", shebang="#!/usr/bin/python", layout="packed")

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

    with setup_tmpdir(build_inputs):
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
                "--no-local-cache",
                "test",
                "oci:oci-test",
            ],
            extra_env={"PANTS_PYTHON_INTERPRETER_CONSTRAINTS": "['CPython==3.9.*']"},
        )

    result.assert_success()
