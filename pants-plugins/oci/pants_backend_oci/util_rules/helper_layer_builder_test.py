from __future__ import annotations

import io
import tarfile

import pytest

from pants_backend_oci.util_rules.deb_layer_builder import DebError
from pants_backend_oci.util_rules.deb_layer_builder_test import _dir, load, make_deb
from pants_backend_oci.util_rules.helper_layer_builder import (
    CA_BUNDLE,
    CA_COPYRIGHT,
    GroupEntry,
    HelperLayerError,
    PasswdEntry,
    build_cacerts_layer,
    build_group_layer,
    build_passwd_layer,
    parse_group_entries,
    parse_passwd_entries,
    render_group,
    render_passwd,
)

USERS = [
    {"name": "root", "uid": 0, "gid": 0, "home": "/root", "shell": "/bin/sh"},
    {"name": "nonroot", "uid": 65532, "gid": 65532, "gecos": "Non Root"},
    {"name": "nobody", "uid": 65534, "gid": 65534, "home": "/nonexistent"},
]
GROUPS = [
    {"name": "root", "gid": 0},
    {"name": "nonroot", "gid": 65532, "members": ["nonroot", "root"]},
]


def layer(build, entries) -> bytes:
    out = io.BytesIO()
    build(entries, out)
    return out.getvalue()


def members(data: bytes) -> list[tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return tar.getmembers()


def read(data: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        extracted = tar.extractfile(name)
        assert extracted is not None
        return extracted.read()


def test_parse_passwd_fills_defaults() -> None:
    entries = parse_passwd_entries(USERS)
    assert entries[1] == PasswdEntry(
        name="nonroot",
        uid=65532,
        gid=65532,
        home="/home/nonroot",
        shell="/sbin/nologin",
        gecos="Non Root",
    )


def test_render_passwd_keeps_order() -> None:
    assert render_passwd(parse_passwd_entries(USERS)) == (
        b"root:x:0:0::/root:/bin/sh\n"
        b"nonroot:x:65532:65532:Non Root:/home/nonroot:/sbin/nologin\n"
        b"nobody:x:65534:65534::/nonexistent:/sbin/nologin\n"
    )


def test_render_group_lists_members() -> None:
    entries = parse_group_entries(GROUPS)
    assert entries[1] == GroupEntry(name="nonroot", gid=65532, members=("nonroot", "root"))
    assert render_group(entries) == b"root:x:0:\nnonroot:x:65532:nonroot,root\n"


@pytest.mark.parametrize(
    "users, error",
    [
        ([], "must not be empty"),
        ("root", "list of dicts"),
        ([{"name": "a", "uid": 1}], "missing gid"),
        ([{"name": "a", "uid": 1, "gid": 1, "group": "a"}], "unknown keys group"),
        ([{"name": "A", "uid": 1, "gid": 1}], "must match"),
        ([{"name": "a", "uid": -1, "gid": 1}], "non-negative integer"),
        ([{"name": "a", "uid": True, "gid": 1}], "non-negative integer"),
        ([{"name": "a", "uid": 1, "gid": 1, "gecos": "a:b"}], "without `:`"),
        ([{"name": "a", "uid": 1, "gid": 1, "gecos": "a\nb"}], "without `:`"),
        ([{"name": "a", "uid": 1, "gid": 1, "home": "home/a"}], "normalized absolute path"),
        ([{"name": "a", "uid": 1, "gid": 1, "home": "/home/../a"}], "normalized absolute path"),
        ([{"name": "a", "uid": 1, "gid": 1, "create_home": "yes"}], "must be a bool"),
        ([{"name": "a", "uid": 1, "gid": 1}, {"name": "a", "uid": 2, "gid": 1}], "user name 'a'"),
        ([{"name": "a", "uid": 1, "gid": 1}, {"name": "b", "uid": 1, "gid": 1}], "uid 1"),
        (
            [
                {"name": "a", "uid": 1, "gid": 1, "home": "/h"},
                {"name": "b", "uid": 2, "gid": 1, "home": "/h"},
            ],
            "home directory '/h' is declared twice; set `create_home` to False",
        ),
    ],
)
def test_parse_passwd_rejects_invalid_users(users, error: str) -> None:
    with pytest.raises(HelperLayerError, match=error):
        parse_passwd_entries(users)


def test_parse_passwd_allows_shared_home_without_creating_it() -> None:
    entries = parse_passwd_entries(
        [
            {"name": "a", "uid": 1, "gid": 1, "home": "/srv"},
            {"name": "b", "uid": 2, "gid": 1, "home": "/srv", "create_home": False},
        ]
    )
    assert [e.home for e in entries] == ["/srv", "/srv"]


@pytest.mark.parametrize(
    "groups, error",
    [
        ([], "must not be empty"),
        ([{"name": "a"}], "missing gid"),
        ([{"name": "a", "gid": 1, "users": []}], "unknown keys users"),
        ([{"name": "a", "gid": 1, "members": "b"}], "must be a list"),
        ([{"name": "a", "gid": 1, "members": ["B"]}], "a member of group 'a'"),
        ([{"name": "a", "gid": 1}, {"name": "a", "gid": 2}], "group name 'a'"),
        ([{"name": "a", "gid": 1}, {"name": "b", "gid": 1}], "gid 1"),
    ],
)
def test_parse_group_rejects_invalid_groups(groups, error: str) -> None:
    with pytest.raises(HelperLayerError, match=error):
        parse_group_entries(groups)


def test_passwd_layer_creates_owned_homes() -> None:
    data = layer(build_passwd_layer, parse_passwd_entries(USERS))
    by_name = {m.name: m for m in members(data)}

    # Parents such as /etc and /home are left to the base image.
    assert list(by_name) == ["etc/passwd", "home/nonroot", "root"]
    assert all(m.mtime == 0 for m in by_name.values())

    home = by_name["home/nonroot"]
    assert home.isdir()
    assert (home.uid, home.gid, home.mode, home.uname) == (65532, 65532, 0o700, "nonroot")
    assert (by_name["root"].uid, by_name["root"].mode) == (0, 0o700)

    passwd = by_name["etc/passwd"]
    assert (passwd.uid, passwd.gid, passwd.mode) == (0, 0, 0o644)
    assert read(data, "etc/passwd") == render_passwd(parse_passwd_entries(USERS))


def test_passwd_layer_skips_homes_when_disabled() -> None:
    entries = parse_passwd_entries([{"name": "app", "uid": 10, "gid": 10, "create_home": False}])
    assert [m.name for m in members(layer(build_passwd_layer, entries))] == ["etc/passwd"]


def test_group_layer_contains_group_file() -> None:
    data = layer(build_group_layer, parse_group_entries(GROUPS))
    assert [m.name for m in members(data)] == ["etc/group"]
    assert read(data, "etc/group") == render_group(parse_group_entries(GROUPS))


def test_layers_are_reproducible() -> None:
    users = parse_passwd_entries(USERS)
    groups = parse_group_entries(GROUPS)
    assert layer(build_passwd_layer, users) == layer(build_passwd_layer, users)
    assert layer(build_group_layer, groups) == layer(build_group_layer, groups)


CERT_B = b"-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n"
# Missing its trailing newline, which `update-ca-certificates` adds.
CERT_A = b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"
CERT_LOCAL = b"-----BEGIN CERTIFICATE-----\nCCCC\n-----END CERTIFICATE-----\n"


def ca_certificates_deb(**kwargs) -> bytes:
    return make_deb(
        "ca-certificates",
        [
            _dir("./usr/"),
            _dir("./usr/share/"),
            _dir("./usr/share/ca-certificates/"),
            _dir("./usr/share/ca-certificates/mozilla/"),
            ("./usr/share/ca-certificates/mozilla/b.crt", CERT_B),
            ("./usr/share/ca-certificates/mozilla/a.crt", CERT_A),
            ("./usr/share/ca-certificates/mozilla/README", b"not a cert\n"),
            _dir("./usr/share/ca-certificates/zz-local/"),
            ("./usr/share/ca-certificates/zz-local/c.crt", CERT_LOCAL),
            _dir("./usr/share/doc/"),
            _dir("./usr/share/doc/ca-certificates/"),
            ("./usr/share/doc/ca-certificates/copyright", b"MPL-2.0\n"),
            ("./usr/sbin/update-ca-certificates", b"#!/bin/sh\n"),
        ],
        architecture="all",
        **kwargs,
    )


def test_cacerts_layer_concatenates_certificates_in_path_order() -> None:
    data = layer(build_cacerts_layer, load(ca_certificates_deb()))

    assert [m.name for m in members(data)] == [CA_BUNDLE, CA_COPYRIGHT]
    assert read(data, CA_BUNDLE) == CERT_A + b"\n" + CERT_B + CERT_LOCAL
    assert read(data, CA_COPYRIGHT) == b"MPL-2.0\n"
    bundle = next(m for m in members(data) if m.name == CA_BUNDLE)
    assert (bundle.uid, bundle.gid, bundle.mode, bundle.mtime) == (0, 0, 0o644, 0)


def test_cacerts_layer_is_reproducible() -> None:
    # Different archive timestamps and compression give the same layer.
    first = layer(build_cacerts_layer, load(ca_certificates_deb()))
    second = layer(build_cacerts_layer, load(ca_certificates_deb(mtime=1, data_member="data.tar.gz")))
    assert first == second


def test_cacerts_layer_rejects_other_packages() -> None:
    deb = make_deb("openssl", [("./usr/bin/openssl", b"\x7fELF")])
    with pytest.raises(DebError, match="is openssl, not ca-certificates"):
        layer(build_cacerts_layer, load(deb))


def test_cacerts_layer_requires_certificates() -> None:
    deb = make_deb("ca-certificates", [("./usr/sbin/update-ca-certificates", b"#!/bin/sh\n")])
    with pytest.raises(DebError, match="no certificates"):
        layer(build_cacerts_layer, load(deb))
