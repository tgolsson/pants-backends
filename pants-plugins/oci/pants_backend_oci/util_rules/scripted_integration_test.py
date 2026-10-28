from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path
from textwrap import dedent

import pytest
from pants.testutil.pants_integration_test import run_pants, setup_tmpdir

DEBIAN = dedent("""\
    oci_pull_images(
        name="debian",
        repository="docker.io/library/debian",
        variants=dict(slim="d83056144b2dd301730d2739635c8cbdeaaae20d6887146434184f8c060f03ce"),
        architecture="amd64",
        os="linux",
        anonymous=True,
    )
    """)

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


def top_layer(archive: Path) -> list[str]:
    """The paths in the image's last layer, which the scripted step added."""
    with tarfile.open(archive) as image:
        index = json.load(image.extractfile("index.json"))
        manifest = json.load(image.extractfile(f"blobs/sha256/{index['manifests'][0]['digest'][7:]}"))
        layer = image.extractfile(f"blobs/sha256/{manifest['layers'][-1]['digest'][7:]}")
        with tarfile.open(fileobj=layer) as tar:
            return tar.getnames()


def pants(tmpdir: str, runner: str, isolated: list[str], *args: str):
    return run_pants(
        [
            "--backend-packages=pants_backend_oci",
            *runner_args(runner),
            *isolated,
            "--pants-ignore=['.python-build-standalone', '.*/', '/dist/', '__pycache__']",
            f"--pants-distdir={tmpdir}/dist",
            *args,
        ]
    )


@RUNNERS
def test_scripted_layer_extract_and_image(runner: str, isolated_pants_args: list[str]) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": DEBIAN
        + dedent("""\
            oci_build_layer(
                name="layer",
                base=[":debian#slim"],
                commands=(
                    "mkdir -p /out/sub && echo built > /out/sub/file && echo skipped > /out/skip"
                    " && echo secret > /out/sub/secret && chmod 600 /out/sub/secret"
                    " && chown -R 65532:65532 /out/sub"
                ),
                outputs=["out"],
                exclude=["./out/skip"],
            )

            oci_build_layer(
                name="globbed",
                base=[":debian#slim"],
                commands="mkdir -p /g/a /g/b && echo a > /g/a/f && echo b > /g/b/f && echo c > /g/b/other",
                outputs=["g/*/f"],
            )

            oci_build_layer(name="no-outputs", base=[":debian#slim"], commands="true")

            oci_extract(name="extract", base=[":debian#slim"], outputs=["etc/debian_version"])

            oci_image_build(
                name="image",
                base=[":debian#slim"],
                packages=[":layer"],
                commands="cat /out/sub/file > /etc/motd && rm -rf /var/lib/apt/lists",
                entrypoint="/bin/sh",
                args=["-c", "cat /etc/motd; test ! -e /var/lib/apt/lists && echo gone"],
            )
            """),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        result = pants(
            tmpdir,
            runner,
            isolated_pants_args,
            "package",
            *(f"{tmpdir}/oci:{t}" for t in ("layer", "globbed", "extract", "image")),
        )
        result.assert_success()
        dist = Path(tmpdir, "dist")
        with tarfile.open(next(dist.glob("*/layer.tar.gz"))) as tar:
            names = tar.getnames()
            assert "./out/sub/file" in names and "./out/skip" not in names
            assert tar.extractfile("./out/sub/file").read() == b"built\n"
            secret = tar.getmember("./out/sub/secret")
            assert (secret.mode, secret.uid, secret.gid) == (0o600, 65532, 65532)
        with tarfile.open(next(dist.glob("*/globbed.tar.gz"))) as tar:
            assert tar.getnames() == [".", "./g", "./g/a", "./g/a/f", "./g/b", "./g/b/f"]
        with tarfile.open(next(dist.glob("*/extract.tar.gz"))) as tar:
            assert tar.getnames()[-1] == "./etc/debian_version"
        assert top_layer(next(dist.glob("*/image.tar"))) == [
            "etc",
            "etc/motd",
            "var",
            "var/lib",
            "var/lib/apt",
            "var/lib/apt/.wh.lists",
        ]

        no_outputs = pants(tmpdir, runner, isolated_pants_args, "package", f"{tmpdir}/oci:no-outputs")
        result = pants(tmpdir, runner, isolated_pants_args, "run", f"{tmpdir}/oci:image")

    no_outputs.assert_failure()
    assert "has no `outputs`" in no_outputs.stderr

    result.assert_success()
    assert result.stdout == "built\ngone\n"


@RUNNERS
def test_failing_command_fails_the_build(runner: str, isolated_pants_args: list[str]) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": DEBIAN
        + dedent("""\
            oci_build_layer(
                name="layer",
                base=[":debian#slim"],
                commands="mkdir -p /out && false",
                outputs=["out"],
            )

            oci_build_layer(
                name="missing",
                base=[":debian#slim"],
                commands="true",
                outputs=["not/there"],
            )
            """),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        failed = pants(tmpdir, runner, isolated_pants_args, "package", f"{tmpdir}/oci:layer")
        missing = pants(tmpdir, runner, isolated_pants_args, "package", f"{tmpdir}/oci:missing")

    failed.assert_failure()
    assert "Run build step for" in failed.stderr
    missing.assert_failure()
    assert "not/there" in missing.stderr


@RUNNERS
def test_list_commands_run_without_a_shell(runner: str, isolated_pants_args: list[str]) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": DEBIAN
        + dedent("""\
            oci_build_layer(
                name="layer",
                base=[":debian#slim"],
                commands=["/usr/bin/install", "-D", "/etc/debian_version", "/out/version"],
                outputs=["out"],
            )

            oci_image_build(
                name="image",
                base=[":debian#slim"],
                commands=["/bin/rm", "-rf", "/var/lib/apt/lists"],
            )
            """),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        # A shell that doesn't exist: list commands must not go through it.
        result = pants(
            tmpdir,
            runner,
            isolated_pants_args,
            "--oci-command-shell=['/no/such/shell']",
            "package",
            f"{tmpdir}/oci:layer",
            f"{tmpdir}/oci:image",
        )
        result.assert_success()
        dist = Path(tmpdir, "dist")
        with tarfile.open(next(dist.glob("*/layer.tar.gz"))) as tar:
            assert tar.getnames() == [".", "./out", "./out/version"]
        assert top_layer(next(dist.glob("*/image.tar"))) == [
            "var",
            "var/lib",
            "var/lib/apt",
            "var/lib/apt/.wh.lists",
        ]
