# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Finding files that a `.deb` layer overwrites in the layers below it.

Each layer is indexed once into a small JSON file: every path with its type and, for regular files,
a sha256 of the content, its whiteouts, which dpkg package owns each path and the packages'
versions. A base image's layers are indexed blob by blob. The check then only compares indexes,
so the layers themselves are read once per layer digest, however many images and lint runs use
them.

This module runs inside a sandboxed process, so it uses only the standard library and never imports
Pants.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import sys
import tarfile
import zlib
from collections import defaultdict
from typing import IO, Any, Mapping

from pants_backend_oci.util_rules.oci_layout_builder import (
    LayoutError,
    _open_layer,
    blob_path,
    load_image,
)

_CHUNK = 1 << 20

WHITEOUT_PREFIX = ".wh."
OPAQUE = ".wh..wh..opq"

DPKG_DIR = "var/lib/dpkg/"
DPKG_STATUS = "var/lib/dpkg/status"
DPKG_INFO = "var/lib/dpkg/info/"
STATUS_DIR = "var/lib/dpkg/status.d/"

# Entry types in an index.
FILE = "f"
SYMLINK = "l"
DIRECTORY = "d"
OTHER = "o"

Index = dict[str, Any]


def clean(path: str) -> str:
    """A tar or dpkg path relative to the root, without `./`, `/` or a trailing slash."""
    return posixpath.normpath("/" + path).lstrip("/")


def _sha256(f: IO[bytes]) -> str:
    h = hashlib.sha256()
    while chunk := f.read(_CHUNK):
        h.update(chunk)
    return h.hexdigest()


def _owned_paths(name: str, content: bytes) -> tuple[str, list[str]] | None:
    """The package and the paths it owns, for a dpkg `.list` file or a distroless `.md5sums`."""
    if name.startswith(DPKG_INFO) and name.endswith(".list"):
        package = name[len(DPKG_INFO) : -len(".list")]
        paths = content.decode("utf-8", "replace").splitlines()
    elif name.startswith(STATUS_DIR) and name.endswith(".md5sums"):
        package = name[len(STATUS_DIR) : -len(".md5sums")]
        paths = [
            line.split(None, 1)[1] for line in content.decode("utf-8", "replace").splitlines() if " " in line
        ]
    else:
        return None
    # `libc6:amd64.list` for multi-arch packages.
    return package.partition(":")[0], [clean(path) for path in paths if clean(path)]


def _versions(content: bytes) -> dict[str, str]:
    """The version of each package in a dpkg `status` file or a distroless `status.d` entry."""
    versions = {}
    for stanza in content.decode("utf-8", "replace").split("\n\n"):
        fields = dict(
            line.split(":", 1) for line in stanza.splitlines() if ":" in line and not line[:1].isspace()
        )
        name, version = fields.get("Package", "").strip(), fields.get("Version", "").strip()
        if name and version:
            versions[name] = version
    return versions


def index_layer(f: IO[bytes]) -> Index:
    """Index the (uncompressed) layer tar streamed from `f`."""
    entries: dict[str, list[str]] = {}
    whiteouts: list[str] = []
    opaque: list[str] = []
    owners: dict[str, str] = {}
    versions: dict[str, str] = {}

    with tarfile.open(fileobj=f, mode="r|") as tar:
        for info in tar:
            path = clean(info.name)
            if not path:
                continue
            parent, base = posixpath.split(path)
            if base == OPAQUE:
                opaque.append(parent)
                continue
            if base.startswith(WHITEOUT_PREFIX):
                whiteouts.append(posixpath.join(parent, base[len(WHITEOUT_PREFIX) :]))
                continue

            if info.isreg():
                source = tar.extractfile(info)
                assert source is not None
                if path == DPKG_STATUS or path.startswith((DPKG_INFO, STATUS_DIR)):
                    content = source.read()
                    entries[path] = [FILE, hashlib.sha256(content).hexdigest()]
                    owned = _owned_paths(path, content)
                    if owned:
                        package, paths = owned
                        owners.update((owned_path, package) for owned_path in paths)
                    elif path == DPKG_STATUS or path.startswith(STATUS_DIR):
                        versions.update(_versions(content))
                else:
                    entries[path] = [FILE, _sha256(source)]
            elif info.islnk():
                # A hardlink has its target's content, and the target comes first in the tar.
                target = entries.get(clean(info.linkname))
                entries[path] = list(target) if target else [OTHER, ""]
            elif info.issym():
                entries[path] = [SYMLINK, info.linkname]
            elif info.isdir():
                entries[path] = [DIRECTORY, ""]
            else:
                entries[path] = [OTHER, ""]

    return {
        "entries": entries,
        "whiteouts": whiteouts,
        "opaque": opaque,
        "owners": owners,
        "versions": versions,
    }


def index_layer_file(path: str) -> Index:
    with _open_layer(path) as f:
        return index_layer(f)  # type: ignore[arg-type]


def layer_blobs(layout: str, tag: str) -> list[str]:
    """The paths of the layer blobs of the image tagged `tag` in `layout`, bottom first."""
    image = load_image(layout, tag)
    return [blob_path(layout, layer["digest"]) for layer in image.manifest["layers"]]


# -- check -------------------------------------------------------------------------------------


class View:
    """The filesystem the layers so far add up to."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[str, str, str]] = {}
        self.owners: dict[str, str] = {}
        self.versions: dict[str, str] = {}
        self._resolved_owners: dict[str, str] | None = None

    def copy(self) -> View:
        view = View()
        view.entries = dict(self.entries)
        view.owners = dict(self.owners)
        view.versions = dict(self.versions)
        return view

    def _remove(self, path: str, keep_self: bool = False) -> None:
        entry = self.entries.get(path)
        if entry and entry[0] != DIRECTORY:
            # Only a directory has anything under it to look for.
            del self.entries[path]
            return
        prefix = f"{path}/" if path else ""
        for existing in [p for p in self.entries if p.startswith(prefix)]:
            del self.entries[existing]
        if not keep_self:
            self.entries.pop(path, None)

    def apply(self, index: Index, label: str, files: bool = True) -> None:
        """Stack `index` on top; without `files`, only its whiteouts, directories and symlinks, and
        not its packages."""
        # Whiteouts hide what's below this layer, never the layer's own entries.
        for directory in index["opaque"]:
            self._remove(directory, keep_self=True)
        for path in index["whiteouts"]:
            self._remove(path)
        for path, (kind, value) in index["entries"].items():
            if files or kind in (DIRECTORY, SYMLINK):
                self.entries[path] = (kind, value, label)
        if files:
            self.owners.update(index["owners"])
            self.versions.update(index["versions"])
        self._resolved_owners = None

    def resolve(self, path: str) -> str:
        """`path` with any symlinked parent directories followed, as with merged-/usr's `/bin`."""
        for _ in range(32):
            parts = path.split("/")
            for i in range(1, len(parts)):
                entry = self.entries.get("/".join(parts[:i]))
                if entry and entry[0] == SYMLINK:
                    target = entry[1]
                    parent = "" if target.startswith("/") else "/".join(parts[: i - 1])
                    path = clean(posixpath.join("/", parent, target, *parts[i:]))
                    break
            else:
                return path
        return path

    def owner(self, path: str) -> str:
        if self._resolved_owners is None:
            self._resolved_owners = {self.resolve(owned): package for owned, package in self.owners.items()}
        return _package(self._resolved_owners.get(path), self.versions)


def _package(name: str | None, versions: Mapping[str, str]) -> str:
    if name is None:
        return "no package"
    return f"{name} {versions[name]}" if name in versions else name


def overwrites(view: View, index: Index) -> list[tuple[str, str, str, str]]:
    """The regular files in `index` that replace a different regular file in `view`, as `(path, lower
    layer, lower owner, upper owner)`.

    dpkg's own records are left out: they differ whenever the packages' metadata does, and a
    different package version shows in its files anyway.
    """
    # The layer's own whiteouts, directories and symlinks decide where its files land, such as a
    # real `/bin` replacing merged-/usr's symlink.
    below = view.copy()
    below.apply(index, "", files=False)
    found = []
    for path, (kind, value) in sorted(index["entries"].items()):
        if kind != FILE:
            continue
        resolved = below.resolve(path)
        if resolved.startswith(DPKG_DIR):
            continue
        lower = below.entries.get(resolved)
        if lower is None or lower[0] != FILE or lower[1] == value:
            continue
        upper_owner = _package(index["owners"].get(path), index["versions"])
        found.append((resolved, lower[2], view.owner(resolved), upper_owner))
    return found


def check(spec: Mapping) -> list[str]:
    """Apply the layers in `spec` bottom up, and report what each checked layer overwrites."""
    view = View()
    lines: list[str] = []
    for layer in spec["layers"]:
        with open(layer["index"], "rb") as f:
            index = json.load(f)
        if layer.get("check"):
            found = overwrites(view, index)
            if found:
                lines.extend(_report(layer["label"], found))
        view.apply(index, layer["label"])
    return lines


def _report(label: str, found: list[tuple[str, str, str, str]]) -> list[str]:
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for path, lower_label, lower_owner, upper_owner in found:
        grouped[(lower_owner, lower_label, upper_owner)].append(path)
    lines = [f"{label} overwrites {len(found)} files from lower layers with different content:"]
    for (lower_owner, lower_label, upper_owner), paths in sorted(grouped.items()):
        lines.append(f"  {lower_owner} (in {lower_label}) replaced by {upper_owner}:")
        lines.extend(f"    /{path}" for path in paths)
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Index layers and find overwritten files.")
    commands = parser.add_subparsers(dest="command", required=True)
    blobs_parser = commands.add_parser("layer-blobs", help="Print an image's layer blob paths as JSON.")
    blobs_parser.add_argument("layout")
    blobs_parser.add_argument("--tag", default="build")
    layer_parser = commands.add_parser("index-layer", help="Index a layer tar.")
    layer_parser.add_argument("layer")
    layer_parser.add_argument("--out", required=True)
    check_parser = commands.add_parser("check", help="Report files checked layers overwrite.")
    check_parser.add_argument("spec")
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            with open(args.spec, "rb") as f:
                lines = check(json.load(f))
            print("\n".join(lines))
            return 1 if lines else 0
        if args.command == "layer-blobs":
            print(json.dumps(layer_blobs(args.layout, args.tag)))
            return 0
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(index_layer_file(args.layer), f, separators=(",", ":"), sort_keys=True)
    except (KeyError, ValueError, OSError, EOFError, zlib.error, tarfile.TarError, LayoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0
