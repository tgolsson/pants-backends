from __future__ import annotations

import base64
import hashlib
import json
import time
from importlib import resources

import pytest

from pants_backend_distroless import openpgp
from pants_backend_distroless.openpgp import (
    Keyring,
    OpenPGPError,
    dearmor,
    verify_cleartext,
    verify_detached,
)

# The fixtures in `testdata` were made with GnuPG, see `testdata/README.md`.
RSA = "7B6CD63D6B9C816FF109BF32B921132DE0E83D0D"
ED25519 = "1F724629F6BD574BCEA91C51101F6007C862E2B2"


def fixture(name: str) -> bytes:
    return resources.files(__package__).joinpath("testdata", name).read_bytes()


def fingerprints(keys) -> list[str]:
    return [key.fingerprint.hex().upper() for key in keys]


def _mpi(value: int) -> bytes:
    return value.bit_length().to_bytes(2, "big") + value.to_bytes((value.bit_length() + 7) // 8, "big")


def _packet(tag: int, body: bytes) -> bytes:
    return bytes([0xC0 | tag, 0xFF]) + len(body).to_bytes(4, "big") + body


def stripped(text: bytes) -> bytes:
    return b"".join(line.rstrip(b" \t") + b"\n" for line in text.splitlines())


def test_loads_binary_and_armored_keyrings() -> None:
    assert fingerprints(Keyring.load(fixture("keyring.gpg")).keys) == [RSA, ED25519]
    assert fingerprints(Keyring.load(fixture("rsa.asc"), fixture("ed25519.gpg")).keys) == [RSA, ED25519]


def test_keyring_without_keys_is_an_error() -> None:
    with pytest.raises(OpenPGPError, match="no RSA or Ed25519 v4 public keys"):
        Keyring.load(fixture("Release.gpg"))


@pytest.mark.parametrize(
    "key",
    [
        # An Ed25519 point MPI that's too long.
        bytes([4, 0, 0, 0, 0, 22, 9]) + bytes.fromhex("2b06010401da470f01") + _mpi(1 << 300),
        # A key packet too long for a v4 fingerprint.
        bytes([4, 0, 0, 0, 0, 1]) + _mpi(3) + _mpi(3) + bytes(0x10000),
    ],
)
def test_malformed_keys_are_skipped(key: bytes) -> None:
    with pytest.raises(OpenPGPError, match="no RSA or Ed25519 v4 public keys"):
        Keyring.load(_packet(6, key))


@pytest.mark.parametrize(
    "keyring, signers",
    [
        ("keyring.gpg", [RSA, ED25519]),
        ("rsa.asc", [RSA]),
        ("ed25519.gpg", [ED25519]),
    ],
)
def test_verify_detached(keyring: str, signers: list[str]) -> None:
    # `Release.gpg` is also signed by `other.asc`, which no keyring here has.
    good = verify_detached(fixture("Release"), fixture("Release.gpg"), Keyring.load(fixture(keyring)))
    assert fingerprints(good) == signers


@pytest.mark.parametrize("keyring", ["keyring.gpg", "rsa.asc", "ed25519.gpg"])
def test_verify_cleartext(keyring: str) -> None:
    # The fixture has a dash-escaped line and trailing whitespace, which text mode doesn't sign.
    expected = stripped(fixture("Release"))
    assert expected != fixture("Release")
    assert verify_cleartext(fixture("InRelease"), Keyring.load(fixture(keyring))) == expected


def test_cleartext_returns_only_what_was_signed() -> None:
    keyring = Keyring.load(fixture("rsa.asc"))
    # Text mode doesn't sign trailing whitespace or line endings, so neither may be returned.
    padded = fixture("InRelease").replace(b"Suite: stable\n", b"Suite: stable \t \n")
    assert verify_cleartext(padded, keyring) == stripped(fixture("Release"))
    crlf = fixture("InRelease").replace(b"\n", b"\r\n")
    assert verify_cleartext(crlf, keyring) == stripped(fixture("Release"))


def test_tampered_data_is_a_bad_signature() -> None:
    keyring = Keyring.load(fixture("rsa.asc"))
    with pytest.raises(OpenPGPError, match=f"Bad signature from {RSA}"):
        verify_detached(fixture("Release") + b"Extra: field\n", fixture("Release.gpg"), keyring)

    tampered = fixture("InRelease").replace(b"Suite: stable", b"Suite: stablE")
    with pytest.raises(OpenPGPError, match=f"Bad signature from {RSA}"):
        verify_cleartext(tampered, keyring)


def test_one_bad_signature_fails_despite_a_good_one() -> None:
    keyring = Keyring.load(fixture("keyring.gpg"))
    signatures = dearmor(fixture("Release.gpg"))
    sha1_signature = dearmor(fixture("Release.sha1.gpg"))
    # The SHA-1 signature is skipped, so this still verifies.
    assert fingerprints(verify_detached(fixture("Release"), signatures + sha1_signature, keyring)) == [
        RSA,
        ED25519,
    ]
    forged = sign(b"something else")
    with pytest.raises(OpenPGPError, match=f"Bad signature from {RSA}"):
        verify_detached(fixture("Release"), signatures + forged, keyring)


def _ed25519_signature(transform) -> bytes:
    """The fixture's Ed25519 signature of `Release`, with `(r, s)` transformed."""
    for tag, body in openpgp._packets(dearmor(fixture("Release.gpg"))):
        signature = openpgp._parse_signature(body)
        if signature.algorithm == 22:
            r, s = signature.values
            tail = len(_mpi(r)) + len(_mpi(s))
            return _packet(2, body[:-tail] + b"".join(_mpi(value) for value in transform(r, s)))
    raise AssertionError("no Ed25519 signature")


def _unreduced(s: int) -> int:
    """`s` plus the group order: the same scalar, encoded non-canonically."""
    scalar = int.from_bytes(s.to_bytes(32, "big"), "little")
    return int.from_bytes(
        (scalar + 2**252 + 27742317777372353535851937790883648493).to_bytes(32, "little"), "big"
    )


@pytest.mark.parametrize(
    "transform",
    [
        lambda r, s: (r ^ 1, s),
        lambda r, s: (r, s ^ 1 << 8),
        # Not a point: y >= p.
        lambda r, s: ((1 << 256) - 1, s),
        # s isn't reduced, see RFC 8032 section 5.1.7.
        lambda r, s: (r, _unreduced(s)),
    ],
)
def test_bad_ed25519_signature(transform) -> None:
    keyring = Keyring.load(fixture("ed25519.gpg"))
    assert fingerprints(
        verify_detached(fixture("Release"), _ed25519_signature(lambda r, s: (r, s)), keyring)
    ) == [ED25519]
    with pytest.raises(OpenPGPError, match=f"Bad signature from {ED25519}"):
        verify_detached(fixture("Release"), _ed25519_signature(transform), keyring)


def test_signature_from_a_key_outside_the_keyring() -> None:
    keyring = Keyring.load(fixture("keyring.gpg"))
    with pytest.raises(OpenPGPError, match=r"No good signature .*isn't in the keyring\)\. The keyring has "):
        verify_detached(fixture("Release"), fixture("Release.other.gpg"), keyring)


def test_sha1_is_unsupported() -> None:
    with pytest.raises(OpenPGPError, match=f"No good signature .*{RSA} uses hash algorithm 2"):
        verify_detached(fixture("Release"), fixture("Release.sha1.gpg"), Keyring.load(fixture("rsa.asc")))


def test_detached_and_cleartext_signature_types_dont_mix() -> None:
    keyring = Keyring.load(fixture("rsa.asc"))
    text = fixture("Release")
    with pytest.raises(OpenPGPError, match="uses signature type 0x01"):
        verify_detached(text, sign(text, text_mode=True), keyring)
    with pytest.raises(OpenPGPError, match="uses signature type 0x00"):
        verify_cleartext(clearsign(text, text_mode=False), keyring)


@pytest.mark.parametrize(
    "message, error",
    [
        (b"Origin: Test\n", "doesn't start with"),
        (b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\nOrigin: Test\n", "Malformed armor header"),
        (b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256", "no blank line"),
        (b"-----BEGIN PGP SIGNED MESSAGE-----\n\nOrigin: Test\n", "no -----BEGIN PGP SIGNATURE-----"),
        (
            b"-----BEGIN PGP SIGNED MESSAGE-----\n\n-----BEGIN PGP PUBLIC KEY BLOCK-----\n",
            "unescaped line",
        ),
    ],
)
def test_malformed_cleartext(message: bytes, error: str) -> None:
    with pytest.raises(OpenPGPError, match=error):
        verify_cleartext(message, Keyring.load(fixture("rsa.asc")))


def test_content_after_the_cleartext_signature_is_rejected() -> None:
    with pytest.raises(OpenPGPError, match="content after"):
        verify_cleartext(fixture("InRelease") + b"Suite: other\n", Keyring.load(fixture("rsa.asc")))


def test_test_signer_matches_gnupg() -> None:
    keyring = Keyring.load(fixture("rsa.asc"))
    text = b"Origin: Test\n- dashed\ntrailing  \n"
    assert verify_cleartext(clearsign(text), keyring) == stripped(text)
    assert fingerprints(verify_detached(text, sign(text), keyring)) == [RSA]


# --- A test signer using the RSA fixture key ------------------------------------------------------


def _rsa_private() -> tuple[int, int, int]:
    key = json.loads(fixture("rsa-private.json"))
    return int(key["n"], 16), int(key["e"], 16), int(key["d"], 16)


def _canonical_text(text: bytes) -> bytes:
    lines = text.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return b"\r\n".join(line.rstrip(b" \t") for line in lines)


def sign(data: bytes, text_mode: bool = False) -> bytes:
    """A binary v4 RSA/SHA-256 signature packet by the `rsa.asc` key over `data`."""
    n, _, d = _rsa_private()
    if text_mode:
        data = _canonical_text(data)
    hashed_subpackets = (
        bytes([22, 33, 4]) + bytes.fromhex(RSA) + bytes([5, 2]) + int(time.time()).to_bytes(4, "big")
    )
    hashed = bytes([4, 0x01 if text_mode else 0x00, 1, 8]) + len(hashed_subpackets).to_bytes(2, "big")
    hashed += hashed_subpackets
    digest = hashlib.sha256(data + hashed + b"\x04\xff" + len(hashed).to_bytes(4, "big")).digest()
    k = (n.bit_length() + 7) // 8
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    encoded = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    s = pow(int.from_bytes(encoded, "big"), d, n)
    return _packet(2, hashed + (0).to_bytes(2, "big") + digest[:2] + _mpi(s))


def armor(packets: bytes) -> bytes:
    encoded = base64.b64encode(packets)
    lines = [encoded[i : i + 64] for i in range(0, len(encoded), 64)]
    return b"-----BEGIN PGP SIGNATURE-----\n\n" + b"\n".join(lines) + b"\n-----END PGP SIGNATURE-----\n"


def clearsign(text: bytes, text_mode: bool = True) -> bytes:
    """`text` as an `InRelease`, cleartext signed by the `rsa.asc` key."""
    escaped = b"".join(b"- " + line if line.startswith(b"-") else line for line in text.splitlines(True))
    signature = sign(text if text_mode else _canonical_text(text), text_mode)
    return b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n" + escaped + armor(signature)
