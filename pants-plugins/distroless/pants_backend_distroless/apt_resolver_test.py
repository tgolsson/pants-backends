from __future__ import annotations

import gzip
import hashlib
import json
import lzma
import random
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pants_backend_distroless import apt_resolver
from pants_backend_distroless.apt_resolver import (
    Index,
    Relation,
    ResolveError,
    Source,
    compare_versions,
    fetch_index,
    lockfile,
    main,
    normalize_inputs,
    parse_packages,
    parse_relations,
    resolve,
)


@pytest.mark.parametrize(
    "lower, higher",
    [
        ("1.0", "1.1"),
        ("1.0", "1.0-1"),
        ("1.0-1", "1.0-2"),
        ("1.0~rc1", "1.0"),
        ("1.0~~", "1.0~"),
        ("1.0", "1.0+deb12u1"),
        ("1.9", "1.10"),
        ("9.0", "1:0.1"),
        ("1.0a", "1.0b"),
        ("1.0", "1.0a"),
        ("2.36-9+deb12u7", "2.36-9+deb12u8"),
        ("3.0.14-1~deb12u1", "3.0.14-1"),
        ("1.2.3-1", "1.2.3a-1"),
    ],
)
def test_compare_versions(lower: str, higher: str) -> None:
    assert compare_versions(lower, higher) < 0
    assert compare_versions(higher, lower) > 0
    assert compare_versions(lower, lower) == 0


@pytest.mark.parametrize("a, b", [("1.0", "1.00"), ("0:1.0", "1.0"), ("1.0-0", "1.0-00")])
def test_equal_versions(a: str, b: str) -> None:
    assert compare_versions(a, b) == 0


def test_parse_relations() -> None:
    assert parse_relations(
        "libc6 (>= 2.34), mawk | awk, python3:any (<< 3.12), foo:amd64, bar (> 1), baz [amd64] <!nocheck>"
    ) == [
        (Relation("libc6", ">=", "2.34"),),
        (Relation("mawk"), Relation("awk")),
        (Relation("python3", "<<", "3.12"),),
        (Relation("foo"),),
        (Relation("bar", ">=", "1"),),
        (Relation("baz"),),
    ]
    assert parse_relations("") == []


def test_parse_relations_rejects_garbage() -> None:
    with pytest.raises(ResolveError, match="Can't parse relation"):
        parse_relations("libc6 (>= 2.34")


def stanza(name: str, version: str = "1.0", **fields: str) -> str:
    fields.setdefault("Architecture", "amd64")
    lines = [f"Package: {name}", f"Version: {version}"]
    lines += [f"{key.replace('_', '-')}: {value}" for key, value in fields.items()]
    lines += [
        f"Filename: pool/main/{name[0]}/{name}/{name}_{version}_{fields['Architecture']}.deb",
        f"SHA256: {hashlib.sha256(f'{name}{version}'.encode()).hexdigest()}",
        "Size: 100",
        "Description: a package",
        " with a long description.",
    ]
    return "\n".join(lines) + "\n"


def index(*stanzas: str, uri: str = "https://example.com/debian") -> Index:
    return Index(parse_packages("\n".join(stanzas), uri))


def names(resolved) -> dict[str, str]:
    return {r.package.name: r.package.version for r in resolved}


def test_parse_packages() -> None:
    (package,) = parse_packages(
        stanza("foo", "1:2.0-1", Pre_Depends="bar", Depends="baz (>= 1)", Provides="qux (= 2)"),
        "https://example.com/debian/",
    )
    assert package.name == "foo"
    assert package.version == "1:2.0-1"
    assert package.url == "https://example.com/debian/pool/main/f/foo/foo_1:2.0-1_amd64.deb"
    assert package.size == 100
    assert package.depends == ((Relation("bar"),), (Relation("baz", ">=", "1"),))
    assert package.provides == (Relation("qux", "=", "2"),)


def test_resolves_transitive_closure() -> None:
    idx = index(
        stanza("jq", Depends="libjq1 (= 1.0)"),
        stanza("libjq1", Depends="libonig5, libc6 (>= 2.34)"),
        stanza("libonig5", Pre_Depends="libc6"),
        stanza("libc6", "2.36"),
        stanza("unrelated"),
    )
    roots, resolved = resolve(idx, ["jq"], [])
    assert roots == ["jq"]
    assert names(resolved) == {"jq": "1.0", "libjq1": "1.0", "libonig5": "1.0", "libc6": "2.36"}
    assert {r.package.name: r.depends for r in resolved}["libjq1"] == ("libc6", "libonig5")


def test_picks_highest_satisfying_version() -> None:
    idx = index(
        stanza("app", Depends="lib (<< 3)"),
        stanza("lib", "1.0"),
        stanza("lib", "2.0"),
        stanza("lib", "3.0"),
    )
    assert names(resolve(idx, ["app"], [])[1])["lib"] == "2.0"
    assert names(resolve(idx, ["lib"], [])[1])["lib"] == "3.0"


def test_highest_version_wins_across_sources_and_earlier_source_wins_ties() -> None:
    first = parse_packages(stanza("lib", "1.0") + "\n" + stanza("dup", "1.0"), "https://a")
    second = parse_packages(stanza("lib", "1.0+u1") + "\n" + stanza("dup", "1.0"), "https://b")
    roots, resolved = resolve(Index([*first, *second]), ["lib", "dup"], [])
    by_name = {r.package.name: r.package for r in resolved}
    assert by_name["lib"].url.startswith("https://b/")
    assert by_name["dup"].url.startswith("https://a/")


def test_alternatives_take_first_satisfiable() -> None:
    idx = index(
        stanza("app", Depends="missing | mawk | gawk"),
        stanza("mawk"),
        stanza("gawk"),
    )
    assert set(names(resolve(idx, ["app"], [])[1])) == {"app", "mawk"}


def test_alternatives_prefer_already_selected() -> None:
    idx = index(
        stanza("app", Depends="mawk | gawk"),
        stanza("mawk"),
        stanza("gawk"),
    )
    assert set(names(resolve(idx, ["app", "gawk"], [])[1])) == {"app", "gawk"}


def test_virtual_package_with_single_provider() -> None:
    idx = index(stanza("app", Depends="awk"), stanza("mawk", Provides="awk"))
    roots, resolved = resolve(idx, ["app"], [])
    assert set(names(resolved)) == {"app", "mawk"}
    assert {r.package.name: r.depends for r in resolved}["app"] == ("mawk",)


def test_virtual_package_prefers_required_then_version_then_name() -> None:
    required = index(
        stanza("app", Depends="awk"),
        stanza("gawk", "9.0", Provides="awk"),
        stanza("mawk", "1.0", Provides="awk", Priority="required"),
    )
    assert "mawk" in names(resolve(required, ["app"], [])[1])

    by_version = index(
        stanza("app", Depends="awk"),
        stanza("aawk", "1.0", Provides="awk"),
        stanza("gawk", "2.0", Provides="awk"),
    )
    assert "gawk" in names(resolve(by_version, ["app"], [])[1])

    by_name = index(
        stanza("app", Depends="awk"),
        stanza("zawk", Provides="awk"),
        stanza("gawk", Provides="awk"),
    )
    assert "gawk" in names(resolve(by_name, ["app"], [])[1])


def test_virtual_package_prefers_selected_provider() -> None:
    idx = index(
        stanza("app", Depends="awk"),
        stanza("gawk", Provides="awk"),
        stanza("mawk", Provides="awk", Priority="required"),
    )
    assert set(names(resolve(idx, ["app", "gawk"], [])[1])) == {"app", "gawk"}


def test_versioned_provides() -> None:
    idx = index(
        stanza("app", Depends="iface (>= 2)"),
        stanza("old", Provides="iface (= 1)"),
        stanza("unversioned", Provides="iface"),
        stanza("new", Provides="iface (= 2)"),
    )
    assert set(names(resolve(idx, ["app"], [])[1])) == {"app", "new"}


def test_real_package_preferred_over_provider() -> None:
    idx = index(stanza("app", Depends="foo"), stanza("foo"), stanza("bar", Provides="foo"))
    assert set(names(resolve(idx, ["app"], [])[1])) == {"app", "foo"}


def test_exclude_is_not_traversed() -> None:
    idx = index(
        stanza("jq", Depends="libc6 (>= 99), awk"),
        stanza("libc6", Depends="libgcc-s1"),
        stanza("libgcc-s1"),
    )
    roots, resolved = resolve(idx, ["jq"], ["libc6", "awk"])
    assert set(names(resolved)) == {"jq"}
    assert resolved[0].depends == ()


def test_requested_and_excluded_is_an_error() -> None:
    with pytest.raises(ResolveError, match="both requested and excluded"):
        resolve(index(stanza("jq")), ["jq"], ["jq"])


def test_unknown_package_is_an_error() -> None:
    with pytest.raises(ResolveError, match="No package named nope"):
        resolve(index(stanza("jq")), ["nope"], [])


def test_unsatisfiable_dependency_names_the_chain() -> None:
    idx = index(stanza("app", Depends="lib"), stanza("lib", Depends="missing (>= 2) | gone"))
    with pytest.raises(
        ResolveError, match=r"Nothing satisfies missing \(>= 2\) \| gone \(needed by lib <- app\)"
    ):
        resolve(idx, ["app"], [])


def test_conflicting_versions_are_an_error() -> None:
    idx = index(
        stanza("a", Depends="lib (= 1.0)"),
        stanza("b", Depends="lib (= 2.0)"),
        stanza("lib", "1.0"),
        stanza("lib", "2.0"),
    )
    with pytest.raises(
        ResolveError,
        match=r"lib \(= 2.0\) \(needed by b\) needs lib 2.0, but the closure already has lib 1.0",
    ):
        resolve(idx, ["a", "b"], [])


def test_conflicting_alternative_falls_through() -> None:
    idx = index(
        stanza("a", Depends="lib (= 1.0)"),
        stanza("b", Depends="lib (>= 2.0) | other"),
        stanza("lib", "1.0"),
        stanza("lib", "2.0"),
        stanza("other"),
    )
    assert names(resolve(idx, ["a", "b"], [])[1]) == {"a": "1.0", "b": "1.0", "lib": "1.0", "other": "1.0"}


def test_virtual_package_uses_highest_provider_version() -> None:
    # The earlier source has the newer version, as with a security suite listed first.
    security = parse_packages(stanza("prov", "1.0-1+deb12u1", Provides="virt"), "https://security")
    main_ = parse_packages(stanza("prov", "1.0-1", Provides="virt"), "https://main")
    app = parse_packages(stanza("app", Depends="virt, prov"), "https://main")
    for packages in ([*security, *main_, *app], [*main_, *security, *app]):
        assert names(resolve(Index(packages), ["app"], [])[1])["prov"] == "1.0-1+deb12u1"


def test_exclude_covers_what_excluded_packages_provide() -> None:
    idx = index(
        stanza("app", Depends="awk"),
        stanza("mawk", Provides="awk"),
        stanza("gawk", Provides="awk"),
    )
    roots, resolved = resolve(idx, ["app"], ["mawk"])
    assert names(resolved) == {"app": "1.0"}
    with pytest.raises(ResolveError, match="awk is both requested and excluded"):
        resolve(idx, ["awk"], ["mawk"])


def test_excluded_packages_are_never_chosen() -> None:
    idx = index(stanza("app", Depends="lib | other"), stanza("lib"), stanza("other"))
    assert names(resolve(idx, ["app"], ["lib"])[1]) == {"app": "1.0"}
    idx = index(
        stanza("app", Depends="virt"), stanza("prov", Provides="virt"), stanza("prov2", Provides="virt")
    )
    assert set(names(resolve(idx, ["app", "prov2"], [])[1])) == {"app", "prov2"}


def test_self_dependency_is_dropped() -> None:
    idx = index(stanza("app", Provides="self", Depends="self"))
    assert resolve(idx, ["app"], [])[1][0].depends == ()


def test_resolution_is_independent_of_input_order() -> None:
    stanzas = [
        stanza("app", Depends="awk, lib1, lib2 | lib3"),
        stanza("tool", Depends="lib3 | lib2, awk"),
        stanza("mawk", Provides="awk"),
        stanza("gawk", Provides="awk"),
        stanza("lib1", Depends="lib2"),
        stanza("lib2"),
        stanza("lib3"),
    ]
    expected = resolve(index(*stanzas), ["app", "tool"], [])
    rng = random.Random(0)
    for _ in range(10):
        rng.shuffle(stanzas)
        assert resolve(index(*stanzas), ["tool", "app"], []) == expected


def test_normalize_inputs() -> None:
    assert normalize_inputs(
        ["https://a  bookworm main  contrib", "https://b x y"], ["arm64", "amd64"], ["b", "a", "a"], []
    ) == {
        "sources": ["https://a bookworm main contrib", "https://b x y"],
        "architectures": ["amd64", "arm64"],
        "packages": ["a", "b"],
        "exclude": [],
    }


def test_lockfile_prefixes_errors_with_arch() -> None:
    inputs = normalize_inputs(["https://a b c"], ["amd64"], ["nope"], [])
    with pytest.raises(ResolveError, match="amd64: No package named nope"):
        lockfile(inputs, {"amd64": index(stanza("jq"))})


def test_source_parse() -> None:
    assert Source.parse("https://a/debian/ bookworm main contrib") == Source(
        "https://a/debian", "bookworm", ("main", "contrib")
    )
    with pytest.raises(ResolveError, match="Expected"):
        Source.parse("https://a/debian bookworm")


def write_repo(root: Path, suite: str, packages: dict[str, str], compress=lzma.compress, ext=".xz") -> None:
    """Writes a minimal apt repository with one `Packages` index per `component/arch` key."""
    dists = root / "dists" / suite
    lines = []
    for key, text in packages.items():
        component, arch = key.split("/")
        path = f"{component}/binary-{arch}/Packages{ext}"
        data = compress(text.encode())
        (dists / path).parent.mkdir(parents=True, exist_ok=True)
        (dists / path).write_bytes(data)
        lines.append(f" {hashlib.sha256(data).hexdigest()} {len(data)} {path}")
    (dists / "Release").write_text(f"Suite: {suite}\nSHA256:\n" + "\n".join(lines) + "\n")


def test_fetch_does_not_retry_client_errors(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def urlopen(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(ResolveError, match="Failed to fetch https://example.com/x: HTTP Error 404"):
        apt_resolver._fetch("https://example.com/x")
    assert len(calls) == 1


def test_fetch_index_verifies_and_decompresses(tmp_path: Path) -> None:
    write_repo(tmp_path, "stable", {"main/amd64": stanza("jq")}, gzip.compress, ".gz")
    (package,) = fetch_index(Source(tmp_path.as_uri(), "stable", ("main",)), "amd64")
    assert package.name == "jq"
    assert package.url == f"{tmp_path.as_uri()}/pool/main/j/jq/jq_1.0_amd64.deb"

    with pytest.raises(ResolveError, match="lists no Packages index for main/binary-arm64"):
        fetch_index(Source(tmp_path.as_uri(), "stable", ("main",)), "arm64")

    (tmp_path / "dists/stable/main/binary-amd64/Packages.gz").write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(ResolveError, match="doesn't match the SHA256"):
        fetch_index(Source(tmp_path.as_uri(), "stable", ("main",)), "amd64")


def test_main_writes_lockfile(tmp_path: Path) -> None:
    write_repo(
        tmp_path / "repo",
        "stable",
        {
            "main/amd64": stanza("jq", Depends="libjq1") + "\n" + stanza("libjq1"),
            "main/arm64": stanza("jq", Architecture="arm64") + "\n" + stanza("doc", Architecture="all"),
        },
    )
    inputs = normalize_inputs([f"{(tmp_path / 'repo').as_uri()} stable main"], ["amd64", "arm64"], ["jq"], [])
    (tmp_path / "request.json").write_text(json.dumps(inputs))

    output = tmp_path / "out" / "lock.json"
    assert main(["--request", str(tmp_path / "request.json"), "--output", str(output)]) == 0

    locked = json.loads(output.read_text())
    assert locked["version"] == 1
    assert locked["inputs"] == inputs
    assert locked["architectures"]["amd64"]["roots"] == ["jq"]
    assert [p["name"] for p in locked["architectures"]["amd64"]["packages"]] == ["jq", "libjq1"]
    assert [p["name"] for p in locked["architectures"]["arm64"]["packages"]] == ["jq"]


def test_main_reports_errors(tmp_path: Path, capsys) -> None:
    write_repo(tmp_path / "repo", "stable", {"main/amd64": stanza("jq")})
    inputs = normalize_inputs([f"{(tmp_path / 'repo').as_uri()} stable main"], ["amd64"], ["nope"], [])
    (tmp_path / "request.json").write_text(json.dumps(inputs))

    assert main(["--request", str(tmp_path / "request.json"), "--output", str(tmp_path / "lock.json")]) == 1
    assert "error: amd64: No package named nope" in capsys.readouterr().err
