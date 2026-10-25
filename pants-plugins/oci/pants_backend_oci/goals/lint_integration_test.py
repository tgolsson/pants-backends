# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from pants.testutil.pants_integration_test import run_pants, setup_tmpdir

from pants_backend_oci.util_rules.deb_layer_builder_test import _dir, make_deb


def foo_deb(content: bytes) -> bytes:
    data = [_dir("./usr/"), _dir("./usr/bin/"), ("./usr/bin/foo", content)]
    return make_deb("foo", data, md5sums=b"abc  usr/bin/foo\n")


def lint(lower: bytes, upper: bytes):
    build_inputs = {
        "BUILD_ROOT": "",
        "oci/note.txt": "note",
        "oci/BUILD": dedent(
            """\
            file(name="lower_deb", source="lower.deb")
            file(name="upper_deb", source="upper.deb")
            file(name="note", source="note.txt")

            oci_deb_layer(name="a_lower", debs=[":lower_deb"])
            oci_deb_layer(name="b_upper", debs=[":upper_deb"])

            # A layer above the deb layers isn't checked, or built.
            oci_layer(name="c_files", packages=[":note"])

            oci_image_build(name="image", base=["//:empty"], layers=[":a_lower", ":b_upper", ":c_files"])
            """
        ),
    }
    with setup_tmpdir(build_inputs) as tmpdir:
        Path(tmpdir, "oci/lower.deb").write_bytes(lower)
        Path(tmpdir, "oci/upper.deb").write_bytes(upper)
        return tmpdir, run_pants(
            [
                "--backend-packages=pants_backend_oci",
                "lint",
                "--only=deb-layer-overwrites",
                f"{tmpdir}/oci:image",
            ]
        )


def test_lint_reports_a_deb_layer_overwriting_a_lower_file() -> None:
    tmpdir, result = lint(foo_deb(b"old"), foo_deb(b"new"))

    result.assert_failure()
    assert f"{tmpdir}/oci:b_upper overwrites 1 files from lower layers" in result.stderr
    assert f"foo 1.0-1 (in {tmpdir}/oci:a_lower) replaced by foo 1.0-1:" in result.stderr
    assert "/usr/bin/foo" in result.stderr


def test_lint_passes_when_overwritten_files_are_identical() -> None:
    _, result = lint(foo_deb(b"same"), foo_deb(b"same"))

    result.assert_success()
