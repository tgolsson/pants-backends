# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Verifies the OpenPGP signatures of apt `InRelease` and `Release.gpg` files.

Only what Debian and Ubuntu archives use is supported: v4 keys and signatures made with RSA
(PKCS#1 v1.5) over SHA-224, SHA-256, SHA-384 or SHA-512, or Ed25519 (legacy EdDSA) over SHA-256,
SHA-384 or SHA-512. A signature verifies when at least one signature is good and made by a key in
the keyring. A bad one from a keyring key fails. Signatures from other keys, or with unsupported
algorithms, are skipped.

Every key in the keyring is trusted as given, like `gpgv` does. Expiry and revocation aren't
evaluated: the Release files of a snapshot are historical, so what matters is which keys signed
them.

This module only uses the standard library, like `apt_resolver`, which runs it in the sandbox.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Iterable, Iterator


class OpenPGPError(Exception):
    pass


# --- Packets --------------------------------------------------------------------------------------

_SIGNATURE = 2
_PUBLIC_KEY = 6
_PUBLIC_SUBKEY = 14

_ARMOR = re.compile(rb"-----BEGIN PGP ([A-Z ]+)-----\r?\n(.*?)-----END PGP \1-----", re.DOTALL)


def dearmor(data: bytes) -> bytes:
    """Returns the binary packets of `data`, concatenating every armored block if it's armored."""
    # Binary packets start with a byte that has the high bit set; armor is ASCII.
    if not data or data[0] & 0x80:
        return data
    packets = b""
    for block in _ARMOR.finditer(data):
        lines = block[2].splitlines()
        if b"" not in [line.strip() for line in lines]:
            raise OpenPGPError("Malformed armor: no blank line after the armor headers")
        body = lines[[line.strip() for line in lines].index(b"") + 1 :]
        # The last line may be the `=XXXX` checksum, which the packets' own structure makes redundant.
        encoded = b"".join(line.strip() for line in body if not line.startswith(b"="))
        try:
            packets += base64.b64decode(encoded, validate=True)
        except ValueError as e:
            raise OpenPGPError(f"Malformed armor: {e}") from e
    if not packets:
        raise OpenPGPError("Malformed armor: no armored block")
    return packets


def _packets(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yields the `(tag, body)` of every packet, in either the old or the new format."""
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(data):
            raise OpenPGPError("Truncated packet")
        chunk = data[pos : pos + n]
        pos += n
        return chunk

    while pos < len(data):
        header = take(1)[0]
        if not header & 0x80:
            raise OpenPGPError("Not an OpenPGP packet")
        if header & 0x40:
            tag, body = header & 0x3F, b""
            while True:
                first = take(1)[0]
                if first < 192:
                    body += take(first)
                elif first < 224:
                    body += take(((first - 192) << 8) + take(1)[0] + 192)
                elif first == 255:
                    body += take(int.from_bytes(take(4), "big"))
                else:
                    # A partial body length: more chunks follow.
                    body += take(1 << (first & 0x1F))
                    continue
                break
        else:
            tag, length_type = (header >> 2) & 0x0F, header & 0x03
            if length_type == 3:
                body = take(len(data) - pos)
            else:
                body = take(int.from_bytes(take(1 << length_type), "big"))
        yield tag, body


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise OpenPGPError("Truncated packet")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def int(self, n: int) -> int:
        return int.from_bytes(self.take(n), "big")

    def mpi(self) -> int:
        return int.from_bytes(self.take((self.int(2) + 7) // 8), "big")


# --- Keys -----------------------------------------------------------------------------------------

_RSA = (1, 3)  # RSA (encrypt or sign) and RSA sign-only.
_EDDSA = 22
_ED25519_OID = bytes.fromhex("2b06010401da470f01")


@dataclass(frozen=True)
class PublicKey:
    fingerprint: bytes
    algorithm: int
    # `(n, e)` for RSA, the 32-byte point for Ed25519.
    material: tuple[int, int] | bytes

    @property
    def keyid(self) -> bytes:
        return self.fingerprint[-8:]


def _parse_public_key(body: bytes) -> PublicKey | None:
    """Parses a v4 public key packet; returns None for versions and algorithms we can't verify."""
    reader = _Reader(body)
    # The fingerprint hashes a two-octet length.
    if reader.int(1) != 4 or len(body) > 0xFFFF:
        return None
    reader.take(4)  # Creation time.
    algorithm = reader.int(1)
    material: tuple[int, int] | bytes
    if algorithm in _RSA:
        material = (reader.mpi(), reader.mpi())
    elif algorithm == _EDDSA:
        if reader.take(reader.int(1)) != _ED25519_OID:
            return None
        point = reader.mpi()
        # The native point encoding, prefixed with 0x40.
        if point >> 256 != 0x40:
            return None
        material = point.to_bytes(33, "big")[1:]
    else:
        return None
    fingerprint = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).digest()
    return PublicKey(fingerprint, algorithm, material)


class Keyring:
    """The public keys and subkeys of some keyring files, binary or armored."""

    def __init__(self, keys: Iterable[PublicKey]) -> None:
        self.keys = tuple(keys)

    @classmethod
    def load(cls, *keyrings: bytes) -> Keyring:
        keys = []
        for data in keyrings:
            for tag, body in _packets(dearmor(data)):
                if tag in (_PUBLIC_KEY, _PUBLIC_SUBKEY):
                    key = _parse_public_key(body)
                    if key is not None:
                        keys.append(key)
        if not keys:
            raise OpenPGPError("The keyring has no RSA or Ed25519 v4 public keys")
        return cls(keys)

    def find(self, fingerprint: bytes | None, keyid: bytes | None) -> PublicKey | None:
        for key in self.keys:
            if key.fingerprint == fingerprint or (fingerprint is None and key.keyid == keyid):
                return key
        return None

    def __str__(self) -> str:
        return ", ".join(key.fingerprint.hex().upper() for key in self.keys)


# --- Signatures -----------------------------------------------------------------------------------

_BINARY = 0x00
_TEXT = 0x01

_HASHES = {8: "sha256", 9: "sha384", 10: "sha512", 11: "sha224"}

# The DER-encoded DigestInfo prefix of EMSA-PKCS1-v1_5, per hash.
_DIGEST_INFO = {
    "sha224": bytes.fromhex("302d300d06096086480165030402040500041c"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}

_ISSUER = 16
_ISSUER_FINGERPRINT = 33
# Subpackets that may be marked critical without changing what a signature over a file means:
# creation time, signature expiration, issuer, and issuer fingerprint.
_KNOWN_SUBPACKETS = {2, 3, _ISSUER, _ISSUER_FINGERPRINT}


@dataclass(frozen=True)
class _Signature:
    type: int
    algorithm: int
    hash: str | None
    # The signed prefix of the packet, hashed after the data.
    hashed: bytes
    fingerprint: bytes | None
    keyid: bytes | None
    left16: bytes
    values: tuple[int, ...]
    unsupported: str | None

    @property
    def signer(self) -> str:
        if self.fingerprint is not None:
            return self.fingerprint.hex().upper()
        return self.keyid.hex().upper() if self.keyid is not None else "an unknown key"


def _subpackets(data: bytes) -> Iterator[tuple[int, bool, bytes]]:
    reader = _Reader(data)
    while reader.pos < len(data):
        first = reader.int(1)
        if first < 192:
            length = first
        elif first < 255:
            length = ((first - 192) << 8) + reader.int(1) + 192
        else:
            length = reader.int(4)
        if length == 0:
            raise OpenPGPError("Empty signature subpacket")
        body = reader.take(length)
        yield body[0] & 0x7F, bool(body[0] & 0x80), body[1:]


def _parse_signature(body: bytes) -> _Signature:
    reader = _Reader(body)
    version = reader.int(1)
    if version != 4:
        return _Signature(0, 0, None, b"", None, None, b"", (), f"a v{version} signature")
    type_, algorithm, hash_id = reader.int(1), reader.int(1), reader.int(1)
    hashed_subpackets = reader.take(reader.int(2))
    hashed = body[: reader.pos]
    unhashed_subpackets = reader.take(reader.int(2))
    left16 = reader.take(2)

    fingerprint = keyid = None
    unsupported = None
    for area, is_hashed in ((hashed_subpackets, True), (unhashed_subpackets, False)):
        for kind, critical, value in _subpackets(area):
            if kind == _ISSUER_FINGERPRINT and value[:1] == b"\x04" and len(value) == 21:
                fingerprint = fingerprint or value[1:]
            elif kind == _ISSUER and len(value) == 8:
                keyid = keyid or value
            if is_hashed and critical and kind not in _KNOWN_SUBPACKETS:
                unsupported = f"a critical subpacket of type {kind}"

    hash_name = _HASHES.get(hash_id)
    if hash_name is None:
        unsupported = unsupported or f"hash algorithm {hash_id}"
    values: tuple[int, ...] = ()
    if algorithm in _RSA:
        values = (reader.mpi(),)
    elif algorithm == _EDDSA:
        values = (reader.mpi(), reader.mpi())
        # EdDSA needs a hash of at least 256 bits (RFC 9580, section 5.2.3.4).
        if hash_name == "sha224":
            unsupported = unsupported or "Ed25519 with SHA-224"
    else:
        unsupported = unsupported or f"public-key algorithm {algorithm}"
    return _Signature(type_, algorithm, hash_name, hashed, fingerprint, keyid, left16, values, unsupported)


def _digest(signature: _Signature, data: bytes) -> bytes:
    assert signature.hash is not None
    h = hashlib.new(signature.hash)
    h.update(data)
    h.update(signature.hashed)
    h.update(b"\x04\xff" + len(signature.hashed).to_bytes(4, "big"))
    return h.digest()


def _verify_rsa(key: PublicKey, signature: _Signature, digest: bytes) -> bool:
    n, e = key.material  # type: ignore[misc]
    (s,) = signature.values
    if s >= n:
        return False
    k = (n.bit_length() + 7) // 8
    digest_info = _DIGEST_INFO[signature.hash] + digest  # type: ignore[index]
    padding = k - len(digest_info) - 3
    if padding < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest_info
    return pow(s, e, n).to_bytes(k, "big") == expected


def _compatible(key: PublicKey, signature: _Signature) -> bool:
    return key.algorithm == signature.algorithm or (key.algorithm in _RSA and signature.algorithm in _RSA)


def _verify(signature: _Signature, key: PublicKey, data: bytes) -> bool:
    digest = _digest(signature, data)
    if digest[:2] != signature.left16:
        return False
    if signature.algorithm in _RSA:
        return _verify_rsa(key, signature, digest)
    r, s = signature.values
    if r.bit_length() > 256 or s.bit_length() > 256:
        return False
    # The MPIs hold the native encodings of R and S.
    point: bytes = key.material  # type: ignore[assignment]
    return _ed25519_verify(point, digest, r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _check(data: bytes, signatures: bytes, keyring: Keyring, signature_type: int) -> list[PublicKey]:
    good, bad, skipped = [], [], []
    parsed = [_parse_signature(body) for tag, body in _packets(signatures) if tag == _SIGNATURE]
    if not parsed:
        raise OpenPGPError("No signatures")
    for signature in parsed:
        if signature.unsupported is None and signature.type != signature_type:
            signature = replace(signature, unsupported=f"signature type {signature.type:#04x}")
        key = keyring.find(signature.fingerprint, signature.keyid)
        if signature.unsupported is not None:
            skipped.append(f"{signature.signer} uses {signature.unsupported}")
        elif key is None:
            skipped.append(f"{signature.signer} isn't in the keyring")
        elif not _compatible(key, signature):
            skipped.append(
                f"{signature.signer} made an algorithm {signature.algorithm} signature"
                f" with an algorithm {key.algorithm} key"
            )
        elif _verify(signature, key, data):
            good.append(key)
        else:
            bad.append(key)
    if bad:
        raise OpenPGPError("Bad signature from " + ", ".join(key.fingerprint.hex().upper() for key in bad))
    if not good:
        raise OpenPGPError(
            f"No good signature from a key in the keyring ({'; '.join(skipped)}). The keyring has {keyring}"
        )
    return good


def verify_detached(data: bytes, signatures: bytes, keyring: Keyring) -> list[PublicKey]:
    """Verifies the binary-mode signatures of a `Release.gpg`; returns the keys of the good ones."""
    return _check(data, dearmor(signatures), keyring, _BINARY)


_CLEARTEXT_BEGIN = b"-----BEGIN PGP SIGNED MESSAGE-----"
_SIGNATURE_BEGIN = b"-----BEGIN PGP SIGNATURE-----"
_SIGNATURE_END = b"-----END PGP SIGNATURE-----"


def verify_cleartext(message: bytes, keyring: Keyring) -> bytes:
    """Verifies a cleartext-signed `InRelease`; returns the signed text, with a final newline.

    Text mode doesn't sign trailing whitespace, so the returned lines have none.
    """
    lines = [line.rstrip(b"\r") for line in message.split(b"\n")]
    if lines[0] != _CLEARTEXT_BEGIN:
        raise OpenPGPError(
            f"Not a cleartext signed message: it doesn't start with {_CLEARTEXT_BEGIN.decode()}"
        )
    pos = 1
    while pos < len(lines) and lines[pos] != b"":
        if not re.fullmatch(rb"Hash: [A-Za-z0-9, ]+", lines[pos]):
            raise OpenPGPError(f"Malformed armor header {lines[pos]!r}")
        pos += 1
    if pos == len(lines):
        raise OpenPGPError("Malformed cleartext signed message: no blank line after the armor headers")
    text = []
    for pos in range(pos + 1, len(lines)):
        line = lines[pos]
        if line == _SIGNATURE_BEGIN:
            break
        if line.startswith(b"- "):
            line = line[2:]
        elif line.startswith(b"-"):
            # Unescaped dashes would let other armor lines into the signed text.
            raise OpenPGPError(f"Malformed cleartext signed message: unescaped line {line!r}")
        text.append(line)
    else:
        raise OpenPGPError(f"Malformed cleartext signed message: no {_SIGNATURE_BEGIN.decode()}")
    rest = lines[pos:]
    if _SIGNATURE_END not in rest or any(line.strip() for line in rest[rest.index(_SIGNATURE_END) + 1 :]):
        raise OpenPGPError(f"Malformed cleartext signed message: content after {_SIGNATURE_END.decode()}")
    signature = b"\n".join(rest[: rest.index(_SIGNATURE_END) + 1])
    # Text mode: lines end in CRLF and lose their trailing whitespace; the last line break is the
    # signature's.
    text = [line.rstrip(b" \t") for line in text]
    _check(b"\r\n".join(text), dearmor(signature), keyring, _TEXT)
    return b"".join(line + b"\n" for line in text)


# --- Ed25519 (RFC 8032, section 6) ----------------------------------------------------------------

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, -1, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

_Point = tuple[int, int, int, int]


def _point_add(a: _Point, b: _Point) -> _Point:
    p, q = (a[1] - a[0]) * (b[1] - b[0]) % _P, (a[1] + a[0]) * (b[1] + b[0]) % _P
    c, d = 2 * a[3] * b[3] * _D % _P, 2 * a[2] * b[2] % _P
    e, f, g, h = q - p, d - c, d + c, q + p
    return e * f, g * h, f * g, e * h


def _point_mul(s: int, point: _Point) -> _Point:
    result: _Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        s >>= 1
    return result


def _point_equal(a: _Point, b: _Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, -1, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _point_decompress(encoded: bytes) -> _Point | None:
    if len(encoded) != 32:
        return None
    y = int.from_bytes(encoded, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return x, y, 1, x * y % _P


_G_Y = 4 * pow(5, -1, _P) % _P
_G_X = _recover_x(_G_Y, 0)
assert _G_X is not None
_G: _Point = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    a = _point_decompress(public)
    r = _point_decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _Q
    return _point_equal(_point_mul(s, _G), _point_add(r, _point_mul(h, a)))
