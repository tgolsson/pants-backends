from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from pants_backend_oci.util_rules.deb_layer_builder import DebError
from pants_backend_oci.util_rules.deb_layer_builder_test import _dir, _link, load, make_deb
from pants_backend_oci.util_rules.helper_layer_builder import (
    CA_BUNDLE,
    CA_COPYRIGHT,
    GroupEntry,
    HelperLayerError,
    PasswdEntry,
    build_cacerts_layer,
    build_group_layer,
    build_locale_layer,
    build_passwd_layer,
    main,
    normalize_locale,
    parse_group_entries,
    parse_locales,
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


LC_CTYPE = b"ctype data"


def libc_bin_deb(name: str = "libc-bin", **kwargs) -> bytes:
    return make_deb(
        name,
        [
            _dir("./usr/"),
            _dir("./usr/bin/"),
            ("./usr/bin/localedef", b"\x7fELF"),
            _dir("./usr/lib/"),
            _dir("./usr/lib/locale/"),
            _dir("./usr/lib/locale/C.utf8/"),
            ("./usr/lib/locale/C.utf8/LC_CTYPE", LC_CTYPE),
            _dir("./usr/lib/locale/C.utf8/LC_MESSAGES/"),
            ("./usr/lib/locale/C.utf8/LC_MESSAGES/SYS_LC_MESSAGES", b"messages"),
            _dir("./usr/lib/locale/en_US.utf8/"),
            # Like `locales-all`, which hardlinks identical categories between locales.
            _link(
                "./usr/lib/locale/en_US.utf8/LC_CTYPE", "./usr/lib/locale/C.utf8/LC_CTYPE", tarfile.LNKTYPE
            ),
            ("./usr/lib/locale/locale-archive", b"archive"),
            _dir("./usr/share/"),
            _dir("./usr/share/doc/"),
            _dir(f"./usr/share/doc/{name}/"),
            (f"./usr/share/doc/{name}/copyright", b"LGPL-2.1\n"),
        ],
        **kwargs,
    )


def locale_layer(debs: list[bytes], locales) -> bytes:
    out = io.BytesIO()
    build_locale_layer([load(deb, source=f"deb{i}") for i, deb in enumerate(debs)], locales, out)
    return out.getvalue()


def test_locale_layer_keeps_only_selected_locales() -> None:
    data = locale_layer([libc_bin_deb()], ["C.UTF-8"])

    assert [m.name for m in members(data)] == [
        "usr/lib/locale/C.utf8",
        "usr/lib/locale/C.utf8/LC_CTYPE",
        "usr/lib/locale/C.utf8/LC_MESSAGES",
        "usr/lib/locale/C.utf8/LC_MESSAGES/SYS_LC_MESSAGES",
        "usr/share/doc/libc-bin/copyright",
    ]
    assert read(data, "usr/lib/locale/C.utf8/LC_CTYPE") == LC_CTYPE
    assert read(data, "usr/share/doc/libc-bin/copyright") == b"LGPL-2.1\n"
    assert all((m.uid, m.gid, m.mtime) == (0, 0, 0) for m in members(data))


def test_locale_layer_resolves_hardlinks_to_unselected_locales() -> None:
    data = locale_layer([libc_bin_deb()], ["en_US.UTF-8"])

    ctype = next(m for m in members(data) if m.name == "usr/lib/locale/en_US.utf8/LC_CTYPE")
    assert ctype.isreg()
    assert read(data, ctype.name) == LC_CTYPE
    assert not any(m.name.startswith("usr/lib/locale/C.utf8") for m in members(data))


def test_locale_layer_is_reproducible() -> None:
    first = locale_layer([libc_bin_deb()], ["C.utf8"])
    second = locale_layer([libc_bin_deb(mtime=1, data_member="data.tar.gz")], ["C.utf8"])
    assert first == second


def test_locale_layer_skips_packages_without_selected_locales() -> None:
    other = make_deb(
        "foo", [_dir("./usr/"), _dir("./usr/share/"), ("./usr/share/doc/foo/copyright", b"MIT\n")]
    )
    data = locale_layer([other, libc_bin_deb()], ["C.utf8"])
    assert "usr/share/doc/foo/copyright" not in [m.name for m in members(data)]


def test_locale_layer_reports_missing_locales() -> None:
    # `locale-archive` is a file, not a locale directory.
    with pytest.raises(
        DebError, match=r"no package ships locale 'de_DE.UTF-8'.*available: C.utf8, en_US.utf8$"
    ):
        locale_layer([libc_bin_deb()], ["C.utf8", "de_DE.UTF-8"])


def test_locale_layer_rejects_locales_from_two_packages() -> None:
    with pytest.raises(DebError, match="locale 'C.utf8' is shipped by both deb0 and deb1"):
        locale_layer([libc_bin_deb(), libc_bin_deb("locales-all")], ["C.utf8"])


def test_locale_layer_writes_directories_the_package_omits() -> None:
    deb = make_deb(
        "libc-bin",
        [
            ("./usr/lib/locale/C.utf8/LC_CTYPE", LC_CTYPE),
            ("./usr/lib/locale/C.utf8/LC_MESSAGES/SYS_LC_MESSAGES", b"messages"),
        ],
    )
    dirs = {m.name: m.mode for m in members(locale_layer([deb], ["C.utf8"])) if m.isdir()}
    assert dirs == {"usr/lib/locale/C.utf8": 0o755, "usr/lib/locale/C.utf8/LC_MESSAGES": 0o755}


def test_locale_layer_selects_several_locales_with_one_copyright() -> None:
    names = [m.name for m in members(locale_layer([libc_bin_deb()], ["C.utf8", "en_US.utf8"]))]
    assert names.count("usr/share/doc/libc-bin/copyright") == 1
    assert {"usr/lib/locale/C.utf8/LC_CTYPE", "usr/lib/locale/en_US.utf8/LC_CTYPE"} <= set(names)


def test_locale_layer_prefers_the_name_as_given() -> None:
    # Like glibc: `C.UTF-8` is tried before its normalized `C.utf8`.
    deb = make_deb(
        "libc-bin",
        [("./usr/lib/locale/C.UTF-8/LC_CTYPE", b"as given"), ("./usr/lib/locale/C.utf8/LC_CTYPE", LC_CTYPE)],
    )
    data = locale_layer([deb], ["C.UTF-8"])
    assert [m.name for m in members(data) if m.isfile()] == ["usr/lib/locale/C.UTF-8/LC_CTYPE"]
    # The directory name itself isn't normalized: `C.UTF-8` is only found as `C.UTF-8`.
    with pytest.raises(DebError, match="no package ships locale 'C.utf8'"):
        locale_layer([make_deb("libc-bin", [("./usr/lib/locale/C.UTF-8/LC_CTYPE", b"x")])], ["C.utf8"])


def test_locale_layer_keeps_symlinks_and_resolves_hardlinks_outside_locales() -> None:
    deb = make_deb(
        "libc-bin",
        [
            ("./usr/share/i18n/ctype", LC_CTYPE),
            ("./usr/lib/locale/C.utf8/LC_TIME", b"time"),
            _link("./usr/lib/locale/C.utf8/LC_CTYPE", "./usr/share/i18n/ctype", tarfile.LNKTYPE),
            _link("./usr/lib/locale/C.utf8/LC_NAME", "LC_TIME", tarfile.SYMTYPE),
        ],
    )
    data = locale_layer([deb], ["C.utf8"])
    by_name = {m.name: m for m in members(data)}
    assert by_name["usr/lib/locale/C.utf8/LC_CTYPE"].isreg()
    assert read(data, "usr/lib/locale/C.utf8/LC_CTYPE") == LC_CTYPE
    assert by_name["usr/lib/locale/C.utf8/LC_NAME"].issym()
    assert by_name["usr/lib/locale/C.utf8/LC_NAME"].linkname == "LC_TIME"
    assert "usr/share/i18n/ctype" not in by_name


def test_locale_layer_rejects_dangling_hardlinks() -> None:
    deb = make_deb(
        "libc-bin", [_link("./usr/lib/locale/C.utf8/LC_CTYPE", "./usr/share/missing", tarfile.LNKTYPE)]
    )
    with pytest.raises(DebError, match="links to /usr/share/missing, which isn't a file in the package"):
        locale_layer([deb], ["C.utf8"])


def test_locale_layer_rejects_paths_listed_twice() -> None:
    deb = make_deb(
        "libc-bin", [("./usr/lib/locale/C.utf8/LC_CTYPE", b"a"), ("./usr/lib/locale/C.utf8/LC_CTYPE", b"b")]
    )
    with pytest.raises(DebError, match="/usr/lib/locale/C.utf8/LC_CTYPE is listed twice"):
        locale_layer([deb], ["C.utf8"])


def test_main_builds_locale_layer(tmp_path: Path) -> None:
    deb = tmp_path / "libc-bin.deb"
    deb.write_bytes(libc_bin_deb())
    output = tmp_path / "out" / "locales.tar"
    assert main(["locales", "--output", str(output), "--locale", "C.UTF-8", str(deb)]) == 0
    assert read(output.read_bytes(), "usr/lib/locale/C.utf8/LC_CTYPE") == LC_CTYPE


def test_main_requires_locales(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deb = tmp_path / "libc-bin.deb"
    deb.write_bytes(libc_bin_deb())
    assert main(["locales", "--output", str(tmp_path / "locales.tar"), str(deb)]) == 1
    assert "locales must not be empty" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["cacerts", "--output", str(tmp_path / "cacerts.tar"), "--locale", "C.utf8", str(deb)])


@pytest.mark.parametrize(
    "layer_name, raw, parse, build",
    [
        ("passwd", USERS, parse_passwd_entries, build_passwd_layer),
        ("group", GROUPS, parse_group_entries, build_group_layer),
    ],
)
def test_main_builds_entries_layer(tmp_path: Path, layer_name: str, raw, parse, build) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(raw))
    output = tmp_path / "out" / f"{layer_name}.tar"
    assert main([layer_name, "--output", str(output), str(spec)]) == 0
    assert output.read_bytes() == layer(build, parse(raw))


def test_main_reports_invalid_entries(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{"name": "app", "uid": 1}]))
    assert main(["passwd", "--output", str(tmp_path / "passwd.tar"), str(spec)]) == 1
    assert capsys.readouterr().err.startswith("error: ")


@pytest.mark.parametrize("spec", [b"not json", b"\xff", None])
def test_main_reports_unreadable_spec(tmp_path: Path, capsys: pytest.CaptureFixture[str], spec) -> None:
    path = tmp_path / "spec.json"
    if spec is not None:
        path.write_bytes(spec)
    assert main(["group", "--output", str(tmp_path / "group.tar"), str(path)]) == 1
    assert capsys.readouterr().err.startswith("error: ")


@pytest.mark.parametrize(
    "name, normalized",
    [
        ("C.UTF-8", "C.utf8"),
        ("C.utf8", "C.utf8"),
        ("en_US.ISO-8859-1", "en_US.iso88591"),
        ("ja_JP.eucJP", "ja_JP.eucjp"),
        ("ru_RU.1251", "ru_RU.iso1251"),
        ("sr_RS.UTF-8@latin", "sr_RS.utf8@latin"),
        ("de_DE@euro", "de_DE@euro"),
    ],
)
def test_normalize_locale(name: str, normalized: str) -> None:
    assert normalize_locale(name) == normalized


@pytest.mark.parametrize(
    "locales, error",
    [
        ([], "must not be empty"),
        ("C.utf8", "must be a list of strings"),
        (["C"], "built into glibc"),
        (["POSIX"], "built into glibc"),
        (["../etc"], "must look like"),
        (["en_US/LC_CTYPE"], "must look like"),
        (["C.-"], "must look like"),
        (["C.utf8", "C.UTF-8"], "locale 'C.utf8' is declared twice"),
    ],
)
def test_parse_locales_rejects_invalid_locales(locales, error: str) -> None:
    with pytest.raises(HelperLayerError, match=error):
        parse_locales(locales)
