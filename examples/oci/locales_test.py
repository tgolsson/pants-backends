"""Golden checks for the `oci_locale_layer` examples, built from the real locked `libc-bin` debs:
bookworm's xz-compressed 2.36-9+deb12u8 and noble's zstd-compressed 2.39-0ubuntu8.3.

The expected digests follow from `examples/oci/debian/bookworm.lock.json` and
`examples/oci/ubuntu/noble.lock.json`; update them when either lockfile bumps `libc-bin` or the
layer format changes."""

from __future__ import annotations

import hashlib
import tarfile

import pytest

LOCALE = "usr/lib/locale/C.utf8"
CATEGORIES = [
    "LC_ADDRESS",
    "LC_COLLATE",
    "LC_CTYPE",
    "LC_IDENTIFICATION",
    "LC_MEASUREMENT",
    "LC_MESSAGES",
    "LC_MESSAGES/SYS_LC_MESSAGES",
    "LC_MONETARY",
    "LC_NAME",
    "LC_NUMERIC",
    "LC_PAPER",
    "LC_TELEPHONE",
    "LC_TIME",
]


@pytest.mark.parametrize(
    "layer, ctype_sha256, layer_sha256",
    [
        pytest.param(
            "examples.oci.debian/locales.tar",
            "e4b5576b19e40be5923b0eb864750d35944404bb0a92aa68d1a9b96110c52120",
            "17d8f0ba715c6daf2c0f07b4097bc7f2733f70aa939447921e1e872a4ebcc3b7",
            id="bookworm",
        ),
        pytest.param(
            "examples.oci.ubuntu/locales.tar",
            "59015993b1b671273461ef1167d7a9ad76dc090d521c91102e962217ed6b6dba",
            "edab3366d298b6f0b987b1e7ba43ecadb8ef6e0cd78d4575c50f8794cf6b76b1",
            id="noble",
        ),
    ],
)
def test_locale_layer(layer: str, ctype_sha256: str, layer_sha256: str) -> None:
    with tarfile.open(layer) as tar:
        # Only `C.utf8` and the copyright file: none of `libc-bin`'s binaries or other files.
        assert tar.getnames() == [
            LOCALE,
            *(f"{LOCALE}/{category}" for category in CATEGORIES),
            "usr/share/doc/libc-bin/copyright",
        ]
        member = tar.extractfile(f"{LOCALE}/LC_CTYPE")
        assert member is not None
        assert hashlib.sha256(member.read()).hexdigest() == ctype_sha256

    with open(layer, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == layer_sha256
