from __future__ import annotations

from textwrap import dedent

from pants.testutil.pants_integration_test import run_pants, setup_tmpdir


def test_run_oci_container() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent(
            """\
                python_sources(name="examples")

                oci_pull_images(
                    name="python3-d11",
                    repository="gcr.io/distroless/python3-debian11",
                    variants={"latest": "42ff8b00a03517f39a968d2e2a6e82c6445586c95e484ff079cbf06f7590cfa7"},
                )

                pex_binary(name="example", entry_point="example.py:main", shebang="#!/usr/bin/python")

                oci_image_build(
                    name="oci",
                    base=[":python3-d11#latest"],
                    packages=[
                        ":example",
                    ],
                )
                """
        ),
        "oci/example.py": dedent(
            """\
            print("Hello world!")
            """
        ),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "run",
                f"{tmpdir}/oci:oci",
            ]
        )

    assert result.stdout == "Hello world!\n"
    assert result.exit_code == 0
