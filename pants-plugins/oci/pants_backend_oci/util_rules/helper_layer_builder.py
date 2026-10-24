# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Builds the small layers a distroless-style image needs instead of running `useradd`, `groupadd`,
`update-ca-certificates` or `locale-gen` in it: `/etc/passwd` (plus home directories), `/etc/group`,
a CA bundle generated from the `ca-certificates` package, and the compiled locales selected from
packages like `libc-bin`.

Layers are deterministic: entries are written in path order with zeroed timestamps and fixed
ownership, so the same input always gives the same bytes. Only the files and home directories
themselves are written, not their parents, so the base image's `/etc`, `/home` or `/tmp` keep their
ownership and modes; a parent missing from the base is created when the image is unpacked.

This module and `deb_layer_builder` only use the standard library. The rules copy both modules into
the sandbox and call `main` for every layer; the CA bundle and locale layers read debs, which may
need a `zstd` binary.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import posixpath
import re
import sys
import tarfile
from dataclasses import dataclass
from typing import Any, BinaryIO, Iterable, Mapping

from pants_backend_oci.util_rules.deb_layer_builder import (
    DebError,
    DebPackage,
    clean_path,
    directory_info,
    file_info,
    load_deb,
    open_data,
)

CA_CERTIFICATES = "ca-certificates"
CA_SOURCE_DIR = "usr/share/ca-certificates"
CA_BUNDLE = "etc/ssl/certs/ca-certificates.crt"
CA_COPYRIGHT = "usr/share/doc/ca-certificates/copyright"

# Where glibc looks for compiled locales, one directory per locale, like `libc-bin`'s `C.utf8`.
LOCALE_DIR = "usr/lib/locale"
DEFAULT_LOCALES = ("C.utf8",)
# `language[_territory][.codeset][@modifier]`, as `setlocale` takes it.
LOCALE_NAME = re.compile(
    r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)?(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)?(?:@[A-Za-z0-9_-]+)?"
)
# Built into glibc, so no package ships them.
BUILTIN_LOCALES = ("C", "POSIX")

# Homes that conventionally mean "no home directory".
NO_HOME = ("/", "/nonexistent")

# Close to `useradd`'s default NAME_REGEX, which every libc can resolve.
NAME = re.compile(r"[a-z_][a-z0-9_.-]*")


class HelperLayerError(ValueError):
    pass


@dataclass(frozen=True)
class PasswdEntry:
    name: str
    uid: int
    gid: int
    home: str
    shell: str = "/sbin/nologin"
    gecos: str = ""
    create_home: bool = True

    def render(self) -> str:
        return f"{self.name}:x:{self.uid}:{self.gid}:{self.gecos}:{self.home}:{self.shell}"


@dataclass(frozen=True)
class GroupEntry:
    name: str
    gid: int
    members: tuple[str, ...] = ()

    def render(self) -> str:
        return f"{self.name}:x:{self.gid}:{','.join(self.members)}"


def _check_keys(what: str, raw: Mapping, required: set[str], optional: set[str]) -> None:
    missing = sorted(required - raw.keys())
    if missing:
        raise HelperLayerError(f"{what} is missing {', '.join(missing)}")
    unknown = sorted(raw.keys() - required - optional)
    if unknown:
        raise HelperLayerError(
            f"{what} has unknown keys {', '.join(map(str, unknown))}; expected "
            f"{', '.join(sorted(required | optional))}"
        )


def _name(what: str, value: Any) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise HelperLayerError(f"{what} must match {NAME.pattern}, got {value!r}")
    return value


def _id(what: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HelperLayerError(f"{what} must be a non-negative integer, got {value!r}")
    return value


def _text(what: str, value: Any) -> str:
    if not isinstance(value, str) or any(c in value for c in ":\n"):
        raise HelperLayerError(f"{what} must be a string without `:` or newlines, got {value!r}")
    return value


def _path(what: str, value: Any) -> str:
    value = _text(what, value)
    if value != "/" and (not value.startswith("/") or posixpath.normpath(value) != value):
        raise HelperLayerError(f"{what} must be a normalized absolute path, got {value!r}")
    return value


def _entries(what: str, raw: Any) -> list[Mapping]:
    # BUILD files give frozen mappings and tuples rather than dicts and lists.
    if not isinstance(raw, (list, tuple)) or not all(isinstance(e, Mapping) for e in raw):
        raise HelperLayerError(f"{what} must be a list of dicts")
    return list(raw)


def _unique(what: str, values: Iterable[Any], hint: str = "") -> None:
    seen = set()
    for value in values:
        if value in seen:
            raise HelperLayerError(f"{what} {value!r} is declared twice{hint}")
        seen.add(value)


def parse_passwd_entries(raw: Any) -> tuple[PasswdEntry, ...]:
    """Validate a `users` field value: a list of dicts with `name`, `uid` and `gid`, and optionally
    `home`, `shell`, `gecos` and `create_home`."""
    entries = []
    for index, user in enumerate(_entries("users", raw)):
        what = f"user {user.get('name', index)!r}"
        _check_keys(what, user, {"name", "uid", "gid"}, {"home", "shell", "gecos", "create_home"})
        name = _name(f"the name of {what}", user["name"])
        create_home = user.get("create_home", True)
        if not isinstance(create_home, bool):
            raise HelperLayerError(f"create_home of {what} must be a bool, got {create_home!r}")
        entries.append(
            PasswdEntry(
                name=name,
                uid=_id(f"the uid of {what}", user["uid"]),
                gid=_id(f"the gid of {what}", user["gid"]),
                home=_path(f"the home of {what}", user.get("home", f"/home/{name}")),
                shell=_path(f"the shell of {what}", user.get("shell", PasswdEntry.shell)),
                gecos=_text(f"the gecos of {what}", user.get("gecos", "")),
                create_home=create_home,
            )
        )
    if not entries:
        raise HelperLayerError("users must not be empty")
    _unique("user name", (e.name for e in entries))
    _unique("uid", (e.uid for e in entries))
    _unique(
        "home directory",
        (e.home for e in entries if _creates_home(e)),
        hint="; set `create_home` to False on all but one of the users sharing it",
    )
    return tuple(entries)


def parse_group_entries(raw: Any) -> tuple[GroupEntry, ...]:
    """Validate a `groups` field value: a list of dicts with `name` and `gid`, and optionally
    `members`, a list of user names."""
    entries = []
    for index, group in enumerate(_entries("groups", raw)):
        what = f"group {group.get('name', index)!r}"
        _check_keys(what, group, {"name", "gid"}, {"members"})
        members = group.get("members", [])
        if not isinstance(members, (list, tuple)):
            raise HelperLayerError(f"the members of {what} must be a list, got {members!r}")
        entries.append(
            GroupEntry(
                name=_name(f"the name of {what}", group["name"]),
                gid=_id(f"the gid of {what}", group["gid"]),
                members=tuple(_name(f"a member of {what}", m) for m in members),
            )
        )
    if not entries:
        raise HelperLayerError("groups must not be empty")
    _unique("group name", (e.name for e in entries))
    _unique("gid", (e.gid for e in entries))
    return tuple(entries)


def render_passwd(entries: Iterable[PasswdEntry]) -> bytes:
    return "".join(f"{e.render()}\n" for e in entries).encode("utf-8")


def render_group(entries: Iterable[GroupEntry]) -> bytes:
    return "".join(f"{e.render()}\n" for e in entries).encode("utf-8")


def _creates_home(entry: PasswdEntry) -> bool:
    return entry.create_home and entry.home not in NO_HOME


def _write_layer(entries: list[tuple[tarfile.TarInfo, bytes | None]], out: BinaryIO) -> None:
    """Write `(info, content)` entries in path order with zeroed timestamps."""
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as layer:
        for info, content in sorted(entries, key=lambda entry: entry[0].name):
            info.mtime = 0
            layer.addfile(info, io.BytesIO(content) if content is not None else None)


def _file(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    return file_info(name, content), content


def build_passwd_layer(entries: Iterable[PasswdEntry], out: BinaryIO) -> None:
    """Write `/etc/passwd` and each user's home directory, owned by the user with mode 0700."""
    entries = tuple(entries)
    homes: list[tuple[tarfile.TarInfo, bytes | None]] = [
        (directory_info(e.home.strip("/"), 0o700, e.uid, e.gid, uname=e.name, gname=""), None)
        for e in entries
        if _creates_home(e)
    ]
    _write_layer([_file("etc/passwd", render_passwd(entries)), *homes], out)


def build_group_layer(entries: Iterable[GroupEntry], out: BinaryIO) -> None:
    """Write `/etc/group`."""
    _write_layer([_file("etc/group", render_group(entries))], out)


def ca_bundle(package: DebPackage) -> tuple[bytes, bytes | None]:
    """Concatenate the certificates `ca-certificates` ships, like `update-ca-certificates` with the
    full certificate list its postinst writes, and return the bundle and the copyright file.

    Certificates are ordered by a bytewise sort of their paths, so the bundle only depends on the
    package. Debian's postinst sorts by locale, so its bundle can order them differently.
    """
    if package.name != CA_CERTIFICATES:
        raise DebError(f"{package.source or package.name} is {package.name}, not {CA_CERTIFICATES}")

    certificates: dict[str, bytes] = {}
    copyright = None
    with open_data(package) as data:
        for member in data:
            path = clean_path(member.name)
            is_cert = path.startswith(f"{CA_SOURCE_DIR}/") and path.endswith(".crt")
            if not member.isreg() or not (is_cert or path == CA_COPYRIGHT):
                continue
            extracted = data.extractfile(member)
            content = extracted.read() if extracted else b""
            if is_cert:
                certificates[path] = content
            else:
                copyright = content

    if not certificates:
        raise DebError(f"{package.source or package.name} has no certificates under /{CA_SOURCE_DIR}")

    bundle = b"".join(
        content if content.endswith(b"\n") else content + b"\n" for _, content in sorted(certificates.items())
    )
    return bundle, copyright


def build_cacerts_layer(package: DebPackage, out: BinaryIO) -> None:
    """Write `/etc/ssl/certs/ca-certificates.crt` (and the package's copyright file)."""
    bundle, copyright = ca_bundle(package)
    files: list[tuple[tarfile.TarInfo, bytes | None]] = [_file(CA_BUNDLE, bundle)]
    if copyright is not None:
        files.append(_file(CA_COPYRIGHT, copyright))
    _write_layer(files, out)


def normalize_locale(name: str) -> str:
    """Normalize a locale name's codeset like glibc does when looking it up, so `C.UTF-8` names the
    `C.utf8` directory: lowercase, keep only letters and digits, and prefix `iso` to digits alone."""
    rest, at, modifier = name.partition("@")
    language, dot, codeset = rest.partition(".")
    if dot:
        codeset = "".join(c for c in codeset.lower() if c.isalnum())
        codeset = f"iso{codeset}" if codeset.isdigit() else codeset
    return f"{language}{dot}{codeset}{at}{modifier}"


def parse_locales(raw: Any) -> tuple[str, ...]:
    """Validate a `locales` field value: a non-empty list of distinct locale names."""
    if not isinstance(raw, (list, tuple)) or not all(isinstance(name, str) for name in raw):
        raise HelperLayerError("locales must be a list of strings")
    if not raw:
        raise HelperLayerError("locales must not be empty")
    for name in raw:
        if name in BUILTIN_LOCALES:
            raise HelperLayerError(f"locale {name!r} is built into glibc, so no package ships it")
        if not LOCALE_NAME.fullmatch(name):
            raise HelperLayerError(
                f"locale {name!r} must look like `language[_territory][.codeset][@modifier]`"
            )
    _unique("locale", (normalize_locale(name) for name in raw))
    return tuple(raw)


def _locale_of(member: tarfile.TarInfo, path: str) -> str | None:
    """The locale directory `path` is or is in, if it is under `/usr/lib/locale/<locale>/`. Other
    files directly in `/usr/lib/locale`, like a `locale-archive`, aren't locales."""
    if not path.startswith(f"{LOCALE_DIR}/"):
        return None
    locale, slash, _ = path[len(LOCALE_DIR) + 1 :].partition("/")
    return locale if locale and (slash or member.isdir()) else None


def _locale_dirs(name: str) -> tuple[str, ...]:
    """The directories glibc tries for a locale name, in order: as given, then normalized."""
    return tuple(dict.fromkeys((name, normalize_locale(name))))


def _read(data: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    extracted = data.extractfile(member)
    return extracted.read() if extracted else b""


def _package_locales(
    package: DebPackage, locales: tuple[str, ...], available: set[str]
) -> tuple[dict[str, str], list[tuple[tarfile.TarInfo, bytes | None]]]:
    """Select `locales` from one package, returning the directory chosen for each locale found and
    the layer entries for them (plus the copyright file)."""
    source = package.source or package.name
    candidates = {directory for name in locales for directory in _locale_dirs(name)}
    copyright_path = f"usr/share/doc/{package.name}/copyright"
    copyright = None
    # Only the candidate locales' members and file contents are kept.
    members: dict[str, dict[str, tarfile.TarInfo]] = {}
    contents: dict[str, bytes] = {}
    with open_data(package) as data:
        for member in data:
            path = clean_path(member.name)
            if path == copyright_path and member.isreg():
                copyright = _read(data, member)
                continue
            locale = _locale_of(member, path)
            if locale is None:
                continue
            available.add(locale)
            if locale not in candidates:
                continue
            listed = members.setdefault(locale, {})
            if path in listed:
                raise DebError(f"{source}: /{path} is listed twice")
            listed[path] = member
            if member.isreg():
                contents[path] = _read(data, member)

    chosen = {}
    for name in locales:
        directory = next((d for d in _locale_dirs(name) if d in members), None)
        if directory is not None:
            chosen[name] = directory
    selected = [(path, member) for d in sorted(set(chosen.values())) for path, member in members[d].items()]

    # Hardlinks to files outside the selected locales, like another locale's in `locales-all`, are
    # resolved by reading the package once more for just their targets.
    missing = {clean_path(m.linkname) for _, m in selected if m.islnk()} - contents.keys()
    if missing:
        with open_data(package) as data:
            for member in data:
                path = clean_path(member.name)
                if path in missing and member.isreg():
                    contents[path] = _read(data, member)

    # The locale directories and those inside them are written whether or not the package lists
    # them; their parents are not, like the other helper layers.
    directories = {f"{LOCALE_DIR}/{d}" for d in chosen.values()}
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    for path, member in selected:
        parent = posixpath.dirname(path)
        while parent.count("/") > LOCALE_DIR.count("/") + 1:
            directories.add(parent)
            parent = posixpath.dirname(parent)
        if member.isdir():
            directories.add(path)
        elif member.issym():
            info = tarfile.TarInfo(path)
            info.type, info.linkname = tarfile.SYMTYPE, member.linkname
            info.mode, info.uname, info.gname = 0o777, "root", "root"
            entries.append((info, None))
        elif member.isreg() or member.islnk():
            target = path if member.isreg() else clean_path(member.linkname)
            if target not in contents:
                raise DebError(f"{source}: /{path} links to /{target}, which isn't a file in the package")
            entries.append(_file(path, contents[target]))
        else:
            raise DebError(f"{source}: /{path} is not a file, directory or symlink")
    entries.extend((directory_info(d), None) for d in directories)
    if chosen and copyright is not None:
        entries.append(_file(copyright_path, copyright))
    return chosen, entries


def locale_files(
    packages: Iterable[DebPackage], locales: Iterable[str]
) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    """Select the compiled `locales` from `packages`, plus the copyright file of each package they
    come from. Like glibc, a locale is looked up as a directory under `/usr/lib/locale` by its name
    as given, then by its normalized name; each must be found in exactly one package. Hardlinks
    become regular files, so the layer doesn't depend on unselected locales."""
    locales = tuple(locales)
    found: dict[str, str] = {}
    available: set[str] = set()
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    for package in packages:
        source = package.source or package.name
        chosen, package_entries = _package_locales(package, locales, available)
        for name in chosen:
            if found.setdefault(name, source) != source:
                raise DebError(f"locale {name!r} is shipped by both {found[name]} and {source}")
        entries.extend(package_entries)

    missing = [name for name in locales if name not in found]
    if missing:
        raise DebError(
            f"no package ships locale {', '.join(map(repr, missing))} under /{LOCALE_DIR}; "
            f"available: {', '.join(sorted(available)) or 'none'}"
        )
    return entries


def build_locale_layer(packages: Iterable[DebPackage], locales: Iterable[str], out: BinaryIO) -> None:
    """Write the selected compiled locales under `/usr/lib/locale`."""
    _write_layer(locale_files(packages, locales), out)


def main(argv: list[str]) -> int:
    """Build the CA bundle layer from the one `ca-certificates` package among `debs`, the locale
    layer from the locales among them, or the passwd or group layer from a JSON list of entries."""
    parser = argparse.ArgumentParser(description="Build an OCI helper layer.")
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--output", required=True)
    debs = argparse.ArgumentParser(add_help=False, parents=[output])
    debs.add_argument("--zstd", help="A zstd binary, for zstd-compressed members.")
    debs.add_argument("debs", nargs="+")
    layers = parser.add_subparsers(dest="layer", required=True)
    layers.add_parser("cacerts", parents=[debs], help="The CA bundle from `ca-certificates`.")
    locales_parser = layers.add_parser("locales", parents=[debs], help="Compiled locales.")
    locales_parser.add_argument(
        "--locale", action="append", default=[], help="A locale to keep (repeatable)."
    )
    for layer in ("passwd", "group"):
        layers.add_parser(layer, parents=[output], help=f"/etc/{layer}.").add_argument("spec")
    args = parser.parse_args(argv)

    try:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        if args.layer in ("passwd", "group"):
            with open(args.spec, "rb") as f:
                raw = json.load(f)
            if args.layer == "passwd":
                users = parse_passwd_entries(raw)
                with open(args.output, "wb") as out:
                    build_passwd_layer(users, out)
            else:
                groups = parse_group_entries(raw)
                with open(args.output, "wb") as out:
                    build_group_layer(groups, out)
            return 0
        with contextlib.ExitStack() as stack:
            packages = [
                load_deb(stack.enter_context(open(path, "rb")), source=path, zstd=args.zstd)
                for path in args.debs
            ]
            if args.layer == "locales":
                locales = parse_locales(args.locale)
                with open(args.output, "wb") as out:
                    build_locale_layer(packages, locales, out)
                return 0
            matching = [package for package in packages if package.name == CA_CERTIFICATES]
            if len(matching) != 1:
                found = ", ".join(p.source for p in matching) or "none"
                raise DebError(f"expected exactly one `{CA_CERTIFICATES}` package, found: {found}")
            with open(args.output, "wb") as out:
                build_cacerts_layer(matching[0], out)
    # `ValueError` covers `HelperLayerError` and malformed or non-UTF-8 JSON.
    except (DebError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
