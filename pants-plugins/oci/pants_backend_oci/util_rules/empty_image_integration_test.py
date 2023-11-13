from __future__ import annotations

import json
import os
import tarfile
from textwrap import dedent

from pants.testutil.pants_integration_test import run_pants, setup_tmpdir


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
                """,
        ),
    }

    with setup_tmpdir(build_inputs) as tmpdir:
        result = run_pants([
            "--backend-packages=pants_backend_oci",
            "--backend-packages=pants.backend.python",
            "package",
            f"{tmpdir}/oci:oci",
        ])

        result.assert_success()

        root = os.path.dirname(os.path.abspath(tmpdir))
        print(os.listdir(f"{root}/dist"))
        output_path = root + f"/dist/{tmpdir}.oci/oci.d"

        with open(f"{output_path}/index.json", "r") as f:
            json_data = json.load(f)

        # only one tag should exist - always, but let's just double-check.
        assert len(json_data["manifests"]) == 1

        # The head digest of the single tag
        digest = json_data["manifests"][0]["digest"][7:]

        # The contents of the head digest describes all layers in the tag
        with open(f"{output_path}/blobs/sha256/{digest}", "r") as f:
            json_data = json.load(f)

        # The digest of the last layer (most recently added)
        layer_digest = json_data["layers"][-1]["digest"][7:]

        # We assume that all layers are tar+gzip, so we need to read tar-from-tar

        with tarfile.open(f"{output_path}/blobs/sha256/{layer_digest}", mode="r") as layer_data:
            # We added a single file above, so should only see that here
            assert layer_data.getnames() == [f"{tmpdir}/oci/file.txt"]

            # For sanity let's double-check that the contents are the same.
            file_contents = layer_data.extractfile(f"{tmpdir}/oci/file.txt").read().decode("utf-8")
            assert file_contents == "hello 🎆"
