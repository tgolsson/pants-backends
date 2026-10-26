# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from textwrap import dedent

import pytest
from pants.testutil.pants_integration_test import run_pants, setup_tmpdir

from pants_backend_oci.goals.lint_integration_test import foo_deb


@pytest.mark.parametrize(
    "parameters, image",
    [
        ("", "image"),
        # `:a_second` gets its parameters filled in from the image's.
        ('tags=parametrize(x=["x"])', "image@tags=x"),
    ],
)
def test_layers_stack_in_declared_order(parameters: str, image: str) -> None:
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/BUILD": dedent(
            f"""\
            file(name="first_deb", source="first.deb")
            file(name="second_deb", source="second.deb")

            oci_deb_layer(name="b_first", debs=[":first_deb"])
            oci_deb_layer(name="a_second", debs=[":second_deb"], {parameters})

            # Not in address order: `a_second` goes on top and its /usr/bin/foo wins.
            oci_image_build(
                name="image", base=["//:empty"], layers=[":b_first", ":a_second"], {parameters}
            )
            """
        ),
    }
    with setup_tmpdir(build_inputs) as tmpdir:
        Path(tmpdir, "oci/first.deb").write_bytes(foo_deb(b"first"))
        Path(tmpdir, "oci/second.deb").write_bytes(foo_deb(b"second"))
        result = run_pants(
            [
                "--backend-packages=pants_backend_oci",
                f"--pants-distdir={tmpdir}/dist",
                "package",
                f"{tmpdir}/oci:{image}",
            ]
        )
        result.assert_success()

        with tarfile.open(next(Path(tmpdir, "dist").glob("*/image*.tar"))) as archive:

            def blob(digest: str):
                return archive.extractfile(f"blobs/sha256/{digest[7:]}")

            index = json.load(archive.extractfile("index.json"))
            manifest = json.load(blob(index["manifests"][0]["digest"]))
            config = json.load(blob(manifest["config"]["digest"]))
            with tarfile.open(fileobj=blob(manifest["layers"][-1]["digest"])) as top:
                members = {m.name.removeprefix("./"): m for m in top.getmembers()}
                foo = top.extractfile(members["usr/bin/foo"]).read()

    comments = [h["comment"].split("/oci:")[1] for h in config["history"] if h.get("comment")]
    assert [c.split("@")[0] for c in comments] == ["b_first", "a_second"]
    assert foo == b"second"
