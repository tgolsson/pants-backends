"""Golden checks for the `oci_cacerts_layer` examples, built from the real locked `ca-certificates`
debs: bookworm's xz-compressed 20230311 and noble's zstd-compressed package.

The expected counts and digests follow from `examples/oci/debian/bookworm.lock.json` and
`examples/oci/ubuntu/noble.lock.json`, and from how `helper_layer_builder` orders and joins the
certificates; update them when either lockfile bumps `ca-certificates` or the bundle format changes."""

from __future__ import annotations

import hashlib
import ssl
import tarfile

import pytest

BUNDLE = "etc/ssl/certs/ca-certificates.crt"
COPYRIGHT = "usr/share/doc/ca-certificates/copyright"


@pytest.mark.parametrize(
    "layer, count, sha256",
    [
        pytest.param(
            "examples.oci.debian/cacerts.tar",
            140,
            "678d423fad6fb240a7d000f757d65fd96c757466ebd64e7031a78a64de26297a",
            id="bookworm",
        ),
        pytest.param(
            "examples.oci.ubuntu/cacerts.tar",
            146,
            "6d84ab71cb726c0641b0af84303c316e3fa50db941dc8507d09045eb2fa5d238",
            id="noble",
        ),
    ],
)
def test_cacerts_layer(layer: str, count: int, sha256: str) -> None:
    with tarfile.open(layer) as tar:
        assert sorted(tar.getnames()) == [BUNDLE, COPYRIGHT]
        member = tar.extractfile(BUNDLE)
        assert member is not None
        bundle = member.read()

    assert bundle.count(b"-----BEGIN CERTIFICATE-----") == count

    # OpenSSL rejects any block that isn't a well-formed certificate, and the store deduplicates, so
    # this checks every block parses to a distinct CA certificate.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=bundle.decode("ascii"))
    stats = context.cert_store_stats()
    assert stats["x509"] == count
    assert stats["x509_ca"] == count

    assert hashlib.sha256(bundle).hexdigest() == sha256
