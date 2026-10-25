# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from pants_backend_oci.util_rules import layer_index as li
from pants_backend_oci.util_rules import oci_layout_builder as b
from pants_backend_oci.util_rules.deb_layer_builder_test import (
    _dir,
    _link,
    _tar,
    layer_bytes,
    load,
    make_deb,
)

FOO_SCRIPT = b"#!/bin/sh\necho foo\n"

# A merged-/usr Debian base, as dpkg leaves it: `foo` lists its file under `/bin`, which is a
# symlink to `usr/bin`.
BASE = _tar(
    [
        _link("./bin", "usr/bin", tarfile.SYMTYPE),
        _dir("./usr/"),
        _dir("./usr/bin/"),
        ("./usr/bin/foo", b"#!/bin/sh\necho old foo\n"),
        ("./usr/bin/same", b"same"),
        _link("./usr/bin/hard", "./usr/bin/foo", tarfile.LNKTYPE),
        ("./var/lib/dpkg/status", b"Package: foo\nStatus: install ok installed\nVersion: 0.9-1\n\n"),
        ("./var/lib/dpkg/info/foo:amd64.list", b"/.\n/bin\n/bin/foo\n/bin/hard\n"),
    ]
)


def index(layer: bytes) -> li.Index:
    return li.index_layer(io.BytesIO(layer))


def base_view(*layers: bytes) -> li.View:
    view = li.View()
    for layer in layers or (BASE,):
        view.apply(index(layer), "base")
    return view


def deb_layer(package: str, files: dict[str, bytes]) -> bytes:
    data = [_dir("./usr/"), _dir("./usr/bin/"), *(("./" + path, content) for path, content in files.items())]
    md5sums = "".join(f"abc  {path}\n" for path in files).encode()
    return layer_bytes([load(make_deb(package, data, md5sums=md5sums))])


def test_index_records_types_owners_and_versions() -> None:
    indexed = index(BASE)

    assert indexed["entries"]["bin"] == [li.SYMLINK, "usr/bin"]
    assert indexed["entries"]["usr/bin"] == [li.DIRECTORY, ""]
    assert indexed["entries"]["usr/bin/hard"] == indexed["entries"]["usr/bin/foo"]
    assert indexed["owners"]["bin/foo"] == "foo"
    assert indexed["versions"] == {"foo": "0.9-1"}


def test_index_reads_distroless_status_entries() -> None:
    indexed = index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT}))

    assert indexed["owners"] == {"usr/bin/foo": "foo"}
    assert indexed["versions"] == {"foo": "1.0-1"}


def test_reports_a_file_replaced_with_different_content() -> None:
    found = li.overwrites(base_view(), index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT})))

    assert found == [("usr/bin/foo", "base", "foo 0.9-1", "foo 1.0-1")]


def test_identical_files_are_not_reported() -> None:
    assert li.overwrites(base_view(), index(deb_layer("same", {"usr/bin/same": b"same"}))) == []


def test_follows_symlinked_parents() -> None:
    upper = index(_tar([("./bin/foo", FOO_SCRIPT)]))

    assert li.overwrites(base_view(), upper) == [("usr/bin/foo", "base", "foo 0.9-1", "no package")]


def test_whited_out_files_are_not_reported() -> None:
    whiteout = _tar([(f"./usr/bin/{li.WHITEOUT_PREFIX}foo", b"")])
    opaque = _tar([(f"./usr/bin/{li.OPAQUE}", b"")])
    upper = index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT}))

    assert li.overwrites(base_view(BASE, whiteout), upper) == []
    assert li.overwrites(base_view(BASE, opaque), upper) == []


def test_only_regular_files_are_reported() -> None:
    upper = index(_tar([_dir("./usr/bin/same/"), _link("./usr/bin/foo", "same", tarfile.SYMTYPE)]))

    assert li.overwrites(base_view(), upper) == []


def test_dpkg_records_are_not_reported() -> None:
    # A distroless-style base with the same package files, but different metadata.
    distroless = index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT}))
    status = f"{li.STATUS_DIR}foo"
    distroless["entries"][status] = [li.FILE, "other"]
    distroless["entries"][f"{status}.md5sums"] = [li.FILE, "other"]
    view = li.View()
    view.apply(distroless, "base")

    assert li.overwrites(view, index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT}))) == []


def test_a_real_directory_replacing_a_symlink_is_followed() -> None:
    # A layer built with `merged_usr=False` on a merged-/usr base replaces the `/bin` symlink.
    upper = index(_tar([_dir("./bin/"), ("./bin/foo", FOO_SCRIPT)]))

    assert li.overwrites(base_view(), upper) == []


def test_the_layers_own_whiteouts_hide_what_it_replaces() -> None:
    upper = index(_tar([(f"./usr/bin/{li.OPAQUE}", b""), ("./usr/bin/foo", FOO_SCRIPT)]))

    assert li.overwrites(base_view(), upper) == []


def test_file_whiteouts_keep_siblings() -> None:
    view = base_view(BASE, _tar([(f"./usr/bin/{li.WHITEOUT_PREFIX}same", b"")]))

    assert "usr/bin/same" not in view.entries
    assert "usr/bin/foo" in view.entries
    assert "usr/bin" in view.entries


def test_resolve_follows_absolute_and_relative_links_and_stops_on_loops() -> None:
    view = li.View()
    view.apply(
        index(
            _tar(
                [
                    _link("./abs", "/usr/lib", tarfile.SYMTYPE),
                    _link("./usr/rel", "../usr/lib", tarfile.SYMTYPE),
                    _link("./loop", "loop", tarfile.SYMTYPE),
                ]
            )
        ),
        "base",
    )

    assert view.resolve("abs/x") == "usr/lib/x"
    assert view.resolve("usr/rel/x") == "usr/lib/x"
    assert view.resolve("loop/x") == "loop/x"


def write_index(path: Path, indexed: li.Index) -> str:
    path.write_text(json.dumps(indexed))
    return str(path)


def test_check_reports_only_checked_layers(tmp_path: Path) -> None:
    upper = index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT}))
    spec = {
        "layers": [
            {"label": "//:base layer 1", "index": write_index(tmp_path / "base.json", index(BASE))},
            # Overwrites the base too, but isn't checked.
            {
                "label": "//:base layer 2",
                "index": write_index(tmp_path / "plain.json", index(_tar([("./usr/bin/foo", b"plain")]))),
            },
            {"label": "//:deb", "index": write_index(tmp_path / "deb.json", upper), "check": True},
            {"label": "//:again", "index": write_index(tmp_path / "again.json", upper), "check": True},
            {
                "label": "//:files",
                "index": write_index(tmp_path / "files.json", index(_tar([("./usr/bin/foo", b"files")]))),
            },
        ]
    }

    assert li.check(spec) == [
        "//:deb overwrites 1 files from lower layers with different content:",
        "  foo 0.9-1 (in //:base layer 2) replaced by foo 1.0-1:",
        "    /usr/bin/foo",
    ]


def test_layer_blobs_lists_every_layer_bottom_first(tmp_path: Path, capsys) -> None:
    layout = str(tmp_path / "build")
    upper = deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT})
    paths = []
    for name, layer in (("base.tar", BASE), ("upper.tar", upper)):
        (tmp_path / name).write_bytes(layer)
        paths.append(str(tmp_path / name))
    b.apply(
        layout,
        "build",
        [
            {"op": "init", "architecture": "amd64", "os": "linux"},
            *({"op": "add_layer", "path": path, "history": None} for path in paths),
        ],
    )

    assert li.main(["layer-blobs", layout]) == 0
    blobs = json.loads(capsys.readouterr().out)
    assert [li.index_layer_file(blob) for blob in blobs] == [index(BASE), index(upper)]


def test_main_reports_a_layer_it_cant_read(tmp_path: Path, capsys) -> None:
    layer = tmp_path / "layer.tar.zst"
    layer.write_bytes(b"\x28\xb5\x2f\xfd" + b"\0" * 16)

    assert li.main(["index-layer", str(layer), "--out", str(tmp_path / "index.json")]) == 2
    assert "zstd" in capsys.readouterr().err


def test_main_fails_when_files_are_overwritten(tmp_path: Path, capsys) -> None:
    spec = tmp_path / "spec.json"
    base = write_index(tmp_path / "base.json", index(BASE))
    deb = write_index(tmp_path / "deb.json", index(deb_layer("foo", {"usr/bin/foo": FOO_SCRIPT})))
    spec.write_text(
        json.dumps({"layers": [{"label": "b", "index": base}, {"label": "d", "index": deb, "check": True}]})
    )

    assert li.main(["check", str(spec)]) == 1
    assert "/usr/bin/foo" in capsys.readouterr().out

    spec.write_text(
        json.dumps({"layers": [{"label": "b", "index": base}, {"label": "d", "index": base, "check": True}]})
    )
    assert li.main(["check", str(spec)]) == 0
