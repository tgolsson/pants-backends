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


def _ed25519_packet() -> bytes:
    for _, body in openpgp._packets(dearmor(fixture("Release.gpg"))):
        if openpgp._parse_signature(body).algorithm == openpgp._EDDSA:
            return body
    raise AssertionError("no Ed25519 signature")


def test_ed25519_with_sha224_is_unsupported() -> None:
    # Version, type, public-key algorithm, then the hash algorithm: 11 is SHA-224.
    body = bytearray(_ed25519_packet())
    body[3] = 11
    with pytest.raises(OpenPGPError, match=f"No good signature .*{ED25519} uses Ed25519 with SHA-224"):
        verify_detached(fixture("Release"), _packet(2, bytes(body)), Keyring.load(fixture("ed25519.gpg")))


def test_signature_algorithm_not_matching_the_key_is_skipped() -> None:
    # An Ed25519 key's signature claiming to be RSA: it can't be verified, so it isn't a bad one.
    body = bytearray(_ed25519_packet())
    body[2] = 1
    with pytest.raises(
        OpenPGPError,
        match=f"No good signature .*{ED25519} made an algorithm 1 signature with an algorithm 22 key",
    ):
        verify_detached(fixture("Release"), _packet(2, bytes(body)), Keyring.load(fixture("ed25519.gpg")))


def test_left16_mismatch_is_a_bad_signature() -> None:
    text = fixture("Release")
    body = bytearray(sign(text)[6:])
    # The left16 follows the hashed and unhashed subpacket areas.
    hashed_end = 6 + int.from_bytes(body[4:6], "big")
    left16 = hashed_end + 2 + int.from_bytes(body[hashed_end : hashed_end + 2], "big")
    assert openpgp._parse_signature(bytes(body)).left16 == body[left16 : left16 + 2]
    body[left16] ^= 0xFF
    with pytest.raises(OpenPGPError, match=f"Bad signature from {RSA}"):
        verify_detached(text, _packet(2, bytes(body)), Keyring.load(fixture("rsa.asc")))


def test_critical_subpackets() -> None:
    keyring = Keyring.load(fixture("rsa.asc"))
    text = fixture("Release")
    notation = bytes([0, 0, 0, 0, 0, 1, 0, 1]) + b"ab"
    # A notation, which could change what the signature means, may only be ignored if not critical.
    with pytest.raises(OpenPGPError, match=f"No good signature .*{RSA} uses a critical subpacket of type 20"):
        verify_detached(
            text, sign(text, hashed_subpackets=ISSUER_FINGERPRINT + _subpacket(0x80 | 20, notation)), keyring
        )
    assert fingerprints(
        verify_detached(
            text, sign(text, hashed_subpackets=ISSUER_FINGERPRINT + _subpacket(20, notation)), keyring
        )
    ) == [RSA]
    # A critical unknown subpacket that isn't signed can be stripped by anyone, so it's ignored.
    assert fingerprints(
        verify_detached(text, sign(text, unhashed_subpackets=_subpacket(0x80 | 20, notation)), keyring)
    ) == [RSA]
    # A known one may be critical.
    critical_issuer = bytes([22, 0x80 | 33, 4]) + bytes.fromhex(RSA)
    assert fingerprints(verify_detached(text, sign(text, hashed_subpackets=critical_issuer), keyring)) == [
        RSA
    ]


def test_issuer_by_key_id_only() -> None:
    # Older GnuPG only names the issuer by key ID, in the unhashed area.
    text = fixture("Release")
    signature = sign(text, hashed_subpackets=b"", unhashed_subpackets=_subpacket(16, bytes.fromhex(RSA)[-8:]))
    assert fingerprints(verify_detached(text, signature, Keyring.load(fixture("rsa.asc")))) == [RSA]


def test_v3_signature_is_unsupported() -> None:
    signature = _packet(2, bytes([3, 5, 0x00]) + bytes(20))
    with pytest.raises(OpenPGPError, match="No good signature .*uses a v3 signature"):
        verify_detached(fixture("Release"), signature, Keyring.load(fixture("rsa.asc")))


def _old_format(tag: int, body: bytes, length_type: int) -> bytes:
    header = bytes([0x80 | tag << 2 | length_type])
    if length_type == 3:
        return header + body
    return header + len(body).to_bytes(1 << length_type, "big") + body


def _partial(tag: int, body: bytes, exponent: int) -> bytes:
    """A new format packet whose body is split into partial chunks of `2**exponent` bytes.

    RFC 4880 only allows partial lengths on data packets, with a first chunk of at least 512 bytes.
    The parser is lenient about both, which this pins.
    """
    chunk = 1 << exponent
    encoded = bytes([0xC0 | tag])
    while len(body) > chunk:
        encoded += bytes([224 + exponent]) + body[:chunk]
        body = body[chunk:]
    # The final chunk has a normal length.
    return encoded + b"\xff" + len(body).to_bytes(4, "big") + body


def test_packet_length_encodings() -> None:
    small, medium, large = b"s" * 10, b"m" * 300, b"l" * 70000
    old = (
        _old_format(2, small, 0)
        + _old_format(2, medium, 1)
        + _old_format(2, large, 2)
        + _old_format(2, small, 3)
    )
    assert list(openpgp._packets(old)) == [(2, small), (2, medium), (2, large), (2, small)]
    partial = _partial(2, medium, 8) + _partial(2, large, 12) + _partial(2, small, 0)
    assert list(openpgp._packets(partial)) == [(2, medium), (2, large), (2, small)]
    with pytest.raises(OpenPGPError, match="Truncated packet"):
        list(openpgp._packets(_old_format(2, medium, 1)[:-1]))


@pytest.mark.parametrize(
    "encode",
    [
        lambda tag, body, last: _old_format(tag, body, 3 if last else 1),
        lambda tag, body, last: _partial(tag, body, 6),
    ],
)
def test_signatures_in_other_packet_encodings(encode) -> None:
    packets = list(openpgp._packets(dearmor(fixture("Release.gpg"))))
    signatures = b"".join(encode(tag, body, i == len(packets) - 1) for i, (tag, body) in enumerate(packets))
    assert list(openpgp._packets(signatures)) == packets
    good = verify_detached(fixture("Release"), signatures, Keyring.load(fixture("keyring.gpg")))
    assert fingerprints(good) == [RSA, ED25519]


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


def _subpacket(kind: int, value: bytes) -> bytes:
    return bytes([len(value) + 1, kind]) + value


ISSUER_FINGERPRINT = _subpacket(33, b"\x04" + bytes.fromhex(RSA))


def sign(
    data: bytes,
    text_mode: bool = False,
    hashed_subpackets: bytes = ISSUER_FINGERPRINT,
    unhashed_subpackets: bytes = b"",
) -> bytes:
    """A binary v4 RSA/SHA-256 signature packet by the `rsa.asc` key over `data`.

    A creation time is appended to `hashed_subpackets`.
    """
    n, _, d = _rsa_private()
    if text_mode:
        data = _canonical_text(data)
    hashed_subpackets += _subpacket(2, int(time.time()).to_bytes(4, "big"))
    hashed = bytes([4, 0x01 if text_mode else 0x00, 1, 8]) + len(hashed_subpackets).to_bytes(2, "big")
    hashed += hashed_subpackets
    digest = hashlib.sha256(data + hashed + b"\x04\xff" + len(hashed).to_bytes(4, "big")).digest()
    k = (n.bit_length() + 7) // 8
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    encoded = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    s = pow(int.from_bytes(encoded, "big"), d, n)
    unhashed = len(unhashed_subpackets).to_bytes(2, "big") + unhashed_subpackets
    return _packet(2, hashed + unhashed + digest[:2] + _mpi(s))


def armor(packets: bytes) -> bytes:
    encoded = base64.b64encode(packets)
    lines = [encoded[i : i + 64] for i in range(0, len(encoded), 64)]
    return b"-----BEGIN PGP SIGNATURE-----\n\n" + b"\n".join(lines) + b"\n-----END PGP SIGNATURE-----\n"


def clearsign(text: bytes, text_mode: bool = True) -> bytes:
    """`text` as an `InRelease`, cleartext signed by the `rsa.asc` key."""
    escaped = b"".join(b"- " + line if line.startswith(b"-") else line for line in text.splitlines(True))
    signature = sign(text if text_mode else _canonical_text(text), text_mode)
    return b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n" + escaped + armor(signature)
