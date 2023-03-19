from __future__ import annotations

import json
import os
import tarfile
from textwrap import dedent

from pants.testutil.pants_integration_test import run_pants, setup_tmpdir
from pants.version import PANTS_SEMVER, Version


def test_package_empty_with_file() -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/file.txt": "hello 🎆",
        "oci/BUILD": dedent(
            """\
                file(name="files", source="file.txt")

                oci_image_build(
                    name="oci",
                    base=["//:empty"],
                    packages=[
                        ":files",
                    ],
                )
                """
        ),
    }

    if PANTS_SEMVER < Version("2.15.0.dev0"):
        build_inputs["oci/BUILD"] = build_inputs["oci/BUILD"].replace("//:empty", ":empty") + dedent(
            """\
            oci_image_empty(name="empty")
            """
        )

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "--backend-packages=pants.backend.python",
                "package",
                f"{tmpdir}/oci:oci",
            ]
        )

        result.assert_success()

        output_path = os.path.dirname(os.path.abspath(tmpdir)) + f"/dist/{tmpdir}_oci_oci.None"

        tar = tarfile.open(output_path)
        json_data = json.load(tar.extractfile("index.json"))

        # only one tag should exist - always, but let's just double-check.
        assert len(json_data["manifests"]) == 1

        # The head digest of the single tag
        digest = json_data["manifests"][0]["digest"][7:]

        # The contents of the head digest describes all layers in the tag
        json_data = json.load(tar.extractfile(f"blobs/sha256/{digest}"))

        # The digest of the last layer (most recently added)
        layer_digest = json_data["layers"][-1]["digest"][7:]

        # We assume that all layers are tar+gzip, so we need to read tar-from-tar
        layer_data = tarfile.open(fileobj=tar.extractfile(f"blobs/sha256/{layer_digest}"))

        # We added a single file above, so should only see that here
        assert layer_data.getnames() == [f"{tmpdir}/oci/file.txt"]

        # For sanity let's double-check that the contents are the same.
        file_contents = layer_data.extractfile(f"{tmpdir}/oci/file.txt").read().decode("utf-8")
        assert file_contents == "hello 🎆"
