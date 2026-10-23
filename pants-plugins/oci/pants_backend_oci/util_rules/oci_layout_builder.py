# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Writes OCI image layouts: creating an empty image, adding layers, editing the config,
normalising pulled layouts and exporting an image as an OCI archive.

This module runs inside a sandboxed process, with the rules only deciding what to do, so it uses
only the standard library and never imports Pants.

Every image is addressed by a tag in the layout's `index.json`. Each change writes new config and
manifest blobs, retags the new manifest and drops blobs nothing references anymore. All output is
deterministic: fixed gzip headers, compact JSON and no clocks.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zlib
from dataclasses import dataclass
from typing import Any, BinaryIO, Iterable, Iterator, Mapping

INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
REF_NAME = "org.opencontainers.image.ref.name"

DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_TO_OCI_MEDIA_TYPES = {
    DOCKER_MANIFEST: MANIFEST_MEDIA_TYPE,
    "application/vnd.docker.container.image.v1+json": CONFIG_MEDIA_TYPE,
    "application/vnd.docker.image.rootfs.diff.tar.gzip": LAYER_MEDIA_TYPE,
    "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip": (
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip"
    ),
}
_INDEX_MEDIA_TYPES = (INDEX_MEDIA_TYPE, "application/vnd.docker.distribution.manifest.list.v2+json")

EPOCH = "1970-01-01T00:00:00Z"
AUTHOR = "pants_backend_oci"
BUILT_BY = "BUILT_BY=pants.oci"

# Config fields that `clear` accepts, as `config.<Field>` like umoci's `--clear`.
CLEARABLE = {
    "config.cmd": "Cmd",
    "config.entrypoint": "Entrypoint",
    "config.env": "Env",
    "config.user": "User",
}

_CHUNK = 1 << 20
_GZIP_LEVEL = 6


class LayoutError(ValueError):
    pass


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def blob_path(layout: str, digest: str) -> str:
    algorithm, _, hex_digest = digest.partition(":")
    if algorithm != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", hex_digest):
        raise LayoutError(f"unsupported blob digest {digest!r}")
    return os.path.join(layout, "blobs", algorithm, hex_digest)


def _replace_file(path: str, data: bytes) -> None:
    """Write `path` through a rename, since inputs in a sandbox may be links to read-only files."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_blob(layout: str, digest: str) -> bytes:
    try:
        with open(blob_path(layout, digest), "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise LayoutError(f"blob {digest} is missing from {layout}") from None


def write_blob(layout: str, data: bytes) -> str:
    digest = _digest(data)
    path = blob_path(layout, digest)
    if not os.path.exists(path):
        _replace_file(path, data)
    return digest


def descriptor(media_type: str, digest: str, size: int) -> dict:
    return {"mediaType": media_type, "digest": digest, "size": size}


# -- index -------------------------------------------------------------------------------------


def read_index(layout: str) -> dict:
    try:
        with open(os.path.join(layout, "index.json"), "rb") as f:
            return json.load(f)
    except FileNotFoundError:
        raise LayoutError(f"{layout} is not an OCI layout: no index.json") from None


def write_index(layout: str, index: Mapping) -> None:
    _replace_file(os.path.join(layout, "index.json"), _json(index))


def tagged_descriptor(index: Mapping, tag: str) -> dict:
    tagged = [m for m in index.get("manifests", []) if m.get("annotations", {}).get(REF_NAME) == tag]
    if len(tagged) != 1:
        raise LayoutError(f"expected one manifest tagged {tag!r} in index.json, found {len(tagged)}")
    return tagged[0]


def tag_manifest(layout: str, tag: str, manifest: bytes, previous: Mapping | None = None) -> str:
    """Point `tag` at `manifest`, writing the manifest blob, and return its digest.

    The new descriptor keeps the other fields of `previous`, such as its platform and annotations.
    """
    digest = write_blob(layout, manifest)
    parsed = json.loads(manifest)
    # Embedded data and alternate URLs describe the old manifest, not this one.
    tagged = {k: v for k, v in (previous or {}).items() if k not in ("data", "urls")}
    tagged.update(descriptor(parsed.get("mediaType", MANIFEST_MEDIA_TYPE), digest, len(manifest)))
    if "artifactType" in tagged:
        tagged["artifactType"] = parsed["config"]["mediaType"]
    tagged["annotations"] = {**tagged.get("annotations", {}), REF_NAME: tag}

    index = read_index(layout)
    manifests = [m for m in index.get("manifests", []) if m.get("annotations", {}).get(REF_NAME) != tag]
    index["manifests"] = [*manifests, tagged]
    write_index(layout, index)
    return digest


def image_digest(layout: str, tag: str) -> str:
    return tagged_descriptor(read_index(layout), tag)["digest"]


class _UnknownReferences(Exception):
    pass


def _referenced(layout: str, desc: Mapping, seen: set[str]) -> None:
    digest = desc["digest"]
    if digest in seen:
        return
    seen.add(digest)
    media_type = desc.get("mediaType")
    if media_type in _INDEX_MEDIA_TYPES:
        for child in json.loads(read_blob(layout, digest)).get("manifests", []):
            _referenced(layout, child, seen)
    elif media_type in (MANIFEST_MEDIA_TYPE, DOCKER_MANIFEST):
        manifest = json.loads(read_blob(layout, digest))
        if "subject" in manifest:
            raise _UnknownReferences(digest)
        for child in [manifest["config"], *manifest.get("layers", [])]:
            seen.add(child["digest"])
    else:
        raise _UnknownReferences(digest)


def collect_garbage(layout: str) -> None:
    """Remove blobs that nothing in `index.json` references.

    A layout holding anything whose references aren't known, such as an artifact manifest, is left
    alone rather than risk removing a blob it needs.
    """
    seen: set[str] = set()
    try:
        for desc in read_index(layout).get("manifests", []):
            _referenced(layout, desc, seen)
    except _UnknownReferences:
        return
    blobs = os.path.join(layout, "blobs")
    for algorithm in sorted(os.listdir(blobs)):
        for name in sorted(os.listdir(os.path.join(blobs, algorithm))):
            path = os.path.join(blobs, algorithm, name)
            if f"{algorithm}:{name}" not in seen and os.path.isfile(path):
                os.remove(path)


# -- archives ----------------------------------------------------------------------------------


def _archive_info(name: str, size: int = 0, directory: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    if directory:
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
    else:
        info.size = size
        info.mode = 0o644
    return info


def write_archive(layout: str, tag: str, archive: str, name: str | None) -> str:
    """Write the image tagged `tag` as an OCI archive holding only its own blobs, and return its
    manifest digest.

    The image is named `name`, if given, in `index.json`, both as its ref name (podman) and its
    containerd image name (`docker load` with OCI support). A Docker `manifest.json` naming it in
    `RepoTags`, like `docker save` writes, lets older `docker load`s read it too.
    """
    desc = tagged_descriptor(read_index(layout), tag)
    if desc.get("mediaType") not in (MANIFEST_MEDIA_TYPE, DOCKER_MANIFEST):
        raise LayoutError(f"expected an image manifest tagged {tag!r}, found {desc.get('mediaType')}")
    manifest = json.loads(read_blob(layout, desc["digest"]))
    blobs = sorted(
        {desc["digest"], manifest["config"]["digest"], *(layer["digest"] for layer in manifest["layers"])}
    )

    def arcname(digest: str) -> str:
        return os.path.relpath(blob_path(layout, digest), layout).replace(os.sep, "/")

    annotations = {k: v for k, v in desc.get("annotations", {}).items() if k != REF_NAME}
    if name:
        annotations.update({REF_NAME: name, "io.containerd.image.name": name})
    entry = {**desc, "annotations": annotations}
    if not annotations:
        del entry["annotations"]
    index = {"schemaVersion": 2, "mediaType": INDEX_MEDIA_TYPE, "manifests": [entry]}
    docker_manifest = [
        {
            "Config": arcname(manifest["config"]["digest"]),
            "RepoTags": [name] if name else None,
            "Layers": [arcname(layer["digest"]) for layer in manifest["layers"]],
        }
    ]
    files = {
        "index.json": _json(index),
        "manifest.json": _json(docker_manifest),
        "oci-layout": _json({"imageLayoutVersion": "1.0.0"}),
    }

    if os.path.dirname(archive):
        os.makedirs(os.path.dirname(archive), exist_ok=True)
    # Sorted entries with zeroed owners and times keep the archive byte-identical between builds.
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(_archive_info("blobs", directory=True))
        tar.addfile(_archive_info("blobs/sha256", directory=True))
        for digest in blobs:
            path = blob_path(layout, digest)
            if not os.path.isfile(path):
                raise LayoutError(f"blob {digest} is missing from {layout}")
            with open(path, "rb") as f:
                tar.addfile(_archive_info(arcname(digest), os.fstat(f.fileno()).st_size), f)
        for file_name, data in sorted(files.items()):
            tar.addfile(_archive_info(file_name, len(data)), io.BytesIO(data))
    return desc["digest"]


# -- images ------------------------------------------------------------------------------------


@dataclass
class Image:
    manifest: dict
    config: dict
    # The index descriptor the image was loaded from, if any.
    descriptor: dict | None = None


def init_layout(layout: str) -> None:
    """Create an empty OCI layout, or keep an existing one."""
    os.makedirs(os.path.join(layout, "blobs", "sha256"), exist_ok=True)
    if not os.path.exists(os.path.join(layout, "oci-layout")):
        _replace_file(os.path.join(layout, "oci-layout"), _json({"imageLayoutVersion": "1.0.0"}))
    if not os.path.exists(os.path.join(layout, "index.json")):
        write_index(layout, {"schemaVersion": 2, "mediaType": INDEX_MEDIA_TYPE, "manifests": []})


def empty_image(architecture: str, os_name: str) -> Image:
    config = {
        "created": EPOCH,
        "author": AUTHOR,
        "architecture": architecture,
        "os": os_name,
        "config": {"Env": [BUILT_BY]},
        "rootfs": {"type": "layers", "diff_ids": []},
    }
    manifest = {"schemaVersion": 2, "mediaType": MANIFEST_MEDIA_TYPE, "config": None, "layers": []}
    return Image(manifest, config)


def load_image(layout: str, tag: str) -> Image:
    desc = tagged_descriptor(read_index(layout), tag)
    if desc.get("mediaType") != MANIFEST_MEDIA_TYPE:
        raise LayoutError(f"{tag!r} is a {desc.get('mediaType')}, not an OCI image manifest")
    manifest = json.loads(read_blob(layout, desc["digest"]))
    config = json.loads(read_blob(layout, manifest["config"]["digest"]))
    return Image(manifest, config, desc)


def save_image(layout: str, tag: str, image: Image) -> str:
    config = _json(image.config)
    image.manifest["config"] = descriptor(CONFIG_MEDIA_TYPE, write_blob(layout, config), len(config))
    return tag_manifest(layout, tag, _json(image.manifest), image.descriptor)


class _HashingWriter:
    def __init__(self, out: BinaryIO) -> None:
        self.out = out
        self.hash = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.hash.update(data)
        self.size += len(data)
        return self.out.write(data)

    def flush(self) -> None:
        self.out.flush()


_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_TAR_BLOCK = 512


class _TarChecked:
    """Reads a stream through, failing unless it starts like a tar archive."""

    def __init__(self, source: BinaryIO, path: str) -> None:
        self.source = source
        self.path = path
        self.checked = False

    def read(self, size: int) -> bytes:
        data = self.source.read(size)
        if not self.checked:
            while len(data) < _TAR_BLOCK and (more := self.source.read(_TAR_BLOCK - len(data))):
                data += more
            # A tar starts with a ustar header, or with an end-of-archive block when it's empty.
            header = data[:_TAR_BLOCK]
            if len(header) < _TAR_BLOCK or (header[257:262] != b"ustar" and header.strip(b"\0")):
                raise LayoutError(f"layer {self.path} is not a tar archive")
            self.checked = True
        return data


@contextlib.contextmanager
def _open_layer(path: str) -> Iterator[_TarChecked]:
    """Open a layer tar, transparently decompressing a gzipped one."""
    with open(path, "rb") as f:
        magic = f.read(4)
        f.seek(0)
        if magic == _ZSTD_MAGIC:
            raise LayoutError(f"layer {path} is zstd-compressed; only plain and gzipped tars are supported")
        if magic[:2] != b"\x1f\x8b":
            yield _TarChecked(f, path)
            return
        with gzip.GzipFile(fileobj=f, mode="rb") as gz:
            yield _TarChecked(gz, path)


def compress_layer(source: BinaryIO | _TarChecked, out: BinaryIO) -> tuple[str, str, int]:
    """Gzip `source` into `out` deterministically.

    Returns the diff_id (digest of the uncompressed tar), the blob digest and the blob size.
    """
    uncompressed = hashlib.sha256()
    writer = _HashingWriter(out)
    # No file name and a zero mtime keep the gzip header independent of where and when it ran.
    with gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0, compresslevel=_GZIP_LEVEL) as gz:
        while chunk := source.read(_CHUNK):
            uncompressed.update(chunk)
            gz.write(chunk)
    return f"sha256:{uncompressed.hexdigest()}", f"sha256:{writer.hash.hexdigest()}", writer.size


LAYER_DESCRIPTOR = "layer.json"


def compress_to_blob(path: str, out_dir: str) -> str:
    """Gzip the layer tar at `path` into `out_dir` as a blob plus a `layer.json` descriptor.

    Compressing runs apart from assembly, so an unchanged layer's blob can be reused. Returns the
    blob digest.
    """
    blobs = os.path.join(out_dir, "blobs", "sha256")
    os.makedirs(blobs, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=blobs, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as out, _open_layer(path) as source:
            try:
                diff_id, digest, size = compress_layer(source, out)
            except (EOFError, zlib.error, gzip.BadGzipFile) as e:
                raise LayoutError(f"layer {path} is not a valid gzip stream: {e}") from e
        os.chmod(tmp, 0o644)
        os.replace(tmp, blob_path(out_dir, digest))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    layer = {**descriptor(LAYER_MEDIA_TYPE, digest, size), "diff_id": diff_id}
    _replace_file(os.path.join(out_dir, LAYER_DESCRIPTOR), _json(layer))
    return digest


def add_blob(layout: str, image: Image, path: str, history: Mapping | None) -> None:
    """Add the layer compressed by `compress_to_blob`, whose descriptor is at `path`."""
    with open(path, "rb") as f:
        layer = json.load(f)
    digest = layer["digest"]
    source = blob_path(os.path.dirname(path), digest)
    target = blob_path(layout, digest)
    if not os.path.exists(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # A sandbox input may be a symlink into the store, which mustn't end up in the layout.
        if os.path.islink(source):
            shutil.copyfile(source, target)
        else:
            os.replace(source, target)
    _append_layer(image, descriptor(layer["mediaType"], digest, layer["size"]), layer["diff_id"], history)


def add_layer(layout: str, image: Image, path: str, history: Mapping | None) -> None:
    """Compress the layer tar at `path` and add it."""
    with tempfile.TemporaryDirectory(dir=layout, prefix=".tmp-") as tmp:
        compress_to_blob(path, tmp)
        add_blob(layout, image, os.path.join(tmp, LAYER_DESCRIPTOR), history)


def _append_layer(image: Image, desc: dict, diff_id: str, history: Mapping | None) -> None:
    image.manifest["layers"].append(desc)
    diff_ids = image.config.setdefault("rootfs", {"type": "layers", "diff_ids": []}).setdefault(
        "diff_ids", []
    )
    entries = image.config.setdefault("history", [])
    # Readers match non-empty history entries to layers in order, so a base without history for
    # its layers gets placeholders before this layer's entry.
    missing = len(diff_ids) - sum(1 for entry in entries if not entry.get("empty_layer"))
    entries.extend({"created": EPOCH} for _ in range(missing))
    diff_ids.append(diff_id)
    entries.append(dict(history or {"created": EPOCH}))


def _set_env(env: list[str], entries: Iterable[str]) -> list[str]:
    """Set each `KEY=value` entry, replacing an existing entry for the same key in place."""
    result = list(env)
    for entry in entries:
        key = entry.partition("=")[0]
        for i, existing in enumerate(result):
            if existing.partition("=")[0] == key:
                result[i] = entry
                break
        else:
            result.append(entry)
    return result


def configure(image: Image, op: Mapping) -> None:
    """Apply a config edit. Unset keys leave the image alone; lists replace the existing value."""
    container = image.config.setdefault("config", {})
    for name in op.get("clear", ()):
        if name not in CLEARABLE:
            raise LayoutError(f"cannot clear {name!r}, expected one of {', '.join(sorted(CLEARABLE))}")
        container.pop(CLEARABLE[name], None)
    if op.get("user") is not None:
        container["User"] = op["user"]
    if op.get("env"):
        container["Env"] = _set_env(container.get("Env") or [], op["env"])
    if op.get("entrypoint") is not None:
        container["Entrypoint"] = list(op["entrypoint"])
    if op.get("cmd") is not None:
        container["Cmd"] = list(op["cmd"])
    if op.get("author") is not None:
        image.config["author"] = op["author"]
    if op.get("created") is not None:
        image.config["created"] = op["created"]
    if op.get("history") is not None:
        image.config.setdefault("history", []).append({**op["history"], "empty_layer": True})


def apply(layout: str, tag: str, ops: Iterable[Mapping]) -> str:
    """Apply `ops` to the image tagged `tag` and return the new manifest digest.

    The ops are `init` (start from an empty image), `add_layer` (a layer tar), `add_blob` (a layer
    compressed by `compress_to_blob`) and `config`.
    """
    image: Image | None = None
    for op in ops:
        kind = op.get("op")
        if kind == "init":
            init_layout(layout)
            image = empty_image(op["architecture"], op["os"])
            continue
        if image is None:
            image = load_image(layout, tag)
        if kind == "add_layer":
            add_layer(layout, image, op["path"], op.get("history"))
        elif kind == "add_blob":
            add_blob(layout, image, op["path"], op.get("history"))
        elif kind == "config":
            configure(image, op)
        else:
            raise LayoutError(f"unknown op {kind!r}")

    if image is None:
        return image_digest(layout, tag)
    digest = save_image(layout, tag, image)
    collect_garbage(layout)
    return digest


# -- pulled layouts ----------------------------------------------------------------------------


def oci_media_type(media_type: str) -> str:
    if media_type.startswith("application/vnd.oci."):
        return media_type
    if media_type not in DOCKER_TO_OCI_MEDIA_TYPES:
        raise LayoutError(f"Cannot convert media type {media_type!r} to OCI")
    return DOCKER_TO_OCI_MEDIA_TYPES[media_type]


def docker_manifest_to_oci(content: bytes) -> bytes:
    """Rewrite a Docker v2 image manifest as an OCI image manifest.

    The config and layer blobs are byte-compatible, so only the media types change.
    """
    manifest = json.loads(content)
    manifest["mediaType"] = oci_media_type(manifest["mediaType"])
    for desc in [manifest["config"], *manifest["layers"]]:
        desc["mediaType"] = oci_media_type(desc["mediaType"])
    return _json(manifest)


def normalize(layout: str, tag: str) -> str:
    """Tag the single manifest of a pulled layout as `tag`, converting a Docker v2 manifest to OCI.

    crane pulls Docker v2 images verbatim and writes no ref name, while the backend addresses an
    OCI manifest by tag.
    """
    index = read_index(layout)
    manifests = index.get("manifests", [])
    if len(manifests) != 1:
        raise LayoutError(f"Expected one manifest in the pulled OCI layout, found {len(manifests)}")
    desc = manifests[0]
    if desc.get("mediaType") not in (MANIFEST_MEDIA_TYPE, DOCKER_MANIFEST):
        raise LayoutError(
            f"Expected an image manifest in the pulled OCI layout, found {desc.get('mediaType')}"
        )
    manifest = read_blob(layout, desc["digest"])
    if desc["mediaType"] == DOCKER_MANIFEST:
        manifest = docker_manifest_to_oci(manifest)
    # The pulled descriptor is replaced by the tagged one.
    write_index(layout, {**index, "manifests": []})
    digest = tag_manifest(layout, tag, manifest, desc)
    collect_garbage(layout)
    return digest


# -- command line ------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    # Everything after `set-cmd --` is the new cmd, verbatim, including further `--`s, so it's
    # split off before argparse sees it.
    cmd: list[str] = []
    if "set-cmd" in argv:
        split = argv.index("set-cmd") + 1
        argv, cmd = argv[:split], argv[split:]
        if cmd[:1] == ["--"]:
            cmd = cmd[1:]

    parser = argparse.ArgumentParser(description="Write OCI image layouts.")
    parser.add_argument("--layout", default="build")
    parser.add_argument("--tag", default="build")
    commands = parser.add_subparsers(dest="command", required=True)
    apply_parser = commands.add_parser("apply", help="Apply a JSON list of ops to the image.")
    apply_parser.add_argument("spec")
    compress_parser = commands.add_parser("compress", help="Gzip a layer tar into a blob.")
    compress_parser.add_argument("layer")
    compress_parser.add_argument("--out", required=True)
    commands.add_parser("normalize", help="Tag and convert a pulled layout.")
    commands.add_parser("digest", help="Print the image's manifest digest.")
    export_parser = commands.add_parser("export", help="Write the image as a named OCI archive.")
    export_parser.add_argument("archive")
    export_parser.add_argument("--name")
    commands.add_parser("set-cmd", help="Replace the image's cmd with the args after `--`, if any.")
    args = parser.parse_args(argv)

    try:
        if args.command == "apply":
            with open(args.spec, "rb") as f:
                digest = apply(args.layout, args.tag, json.load(f))
        elif args.command == "compress":
            digest = compress_to_blob(args.layer, args.out)
        elif args.command == "normalize":
            digest = normalize(args.layout, args.tag)
        elif args.command == "export":
            digest = write_archive(args.layout, args.tag, args.archive, args.name)
        elif args.command == "set-cmd":
            ops = [{"op": "config", "cmd": cmd}] if cmd else []
            digest = apply(args.layout, args.tag, ops)
        else:
            digest = image_digest(args.layout, args.tag)
    except (KeyError, ValueError, OSError, EOFError, zlib.error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(digest)
    return 0
