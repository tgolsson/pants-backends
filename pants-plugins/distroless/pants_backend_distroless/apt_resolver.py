# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Resolves Debian packages against apt repository snapshots into a lockfile.

Each source is a sources.list line without the leading `deb`: `<uri> <suite> <component>...`. The
`InRelease` file of every suite is fetched and its signature verified against the given keyrings,
falling back to `Release` and `Release.gpg` where there's no `InRelease`, and its `Suite` or
`Codename` must name the requested suite by apt's rule (where apt only warns). Each `Packages`
index is then checked against the SHA256 the verified file lists before being parsed. Resolution
then walks the `Pre-Depends` and `Depends` of the requested packages, much like rules_distroless
does:

* a real package resolves to its highest version (across all sources) satisfying the constraint;
* `a | b` takes the first satisfiable alternative, unless one is already in the closure;
* a virtual package prefers a provider already in the closure, then the only provider, then a
  `Priority: required` one, then the highest version, then the first by name;
* excluded packages count as installed (they satisfy any constraint) and are never traversed.

This module only uses the standard library: the lockfile rules copy it into the sandbox together
with `openpgp` and call `main`, and both the rules and the tests import it directly.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import json
import lzma
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from . import openpgp

LOCKFILE_VERSION = 2


class ResolveError(Exception):
    pass


class NotFound(ResolveError):
    pass


# --- Debian versions ------------------------------------------------------------------------------


def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _compare_fragment(a: str, b: str) -> int:
    """Compares an upstream version or revision with the dpkg algorithm."""
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            bc = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def _split_version(version: str) -> tuple[int, str, str]:
    epoch, sep, rest = version.partition(":")
    if not sep:
        epoch, rest = "0", version
    upstream, sep, revision = rest.rpartition("-")
    if not sep:
        upstream, revision = rest, ""
    return int(epoch or "0"), upstream, revision


def compare_versions(a: str, b: str) -> int:
    """Returns a negative, zero or positive number as Debian version `a` is lower, equal or higher."""
    a_epoch, a_upstream, a_revision = _split_version(a)
    b_epoch, b_upstream, b_revision = _split_version(b)
    if a_epoch != b_epoch:
        return a_epoch - b_epoch
    return _compare_fragment(a_upstream, b_upstream) or _compare_fragment(a_revision, b_revision)


_OPERATORS: dict[str, Callable[[int], bool]] = {
    "<<": lambda c: c < 0,
    "<=": lambda c: c <= 0,
    "=": lambda c: c == 0,
    ">=": lambda c: c >= 0,
    ">>": lambda c: c > 0,
}


# --- Relationship fields --------------------------------------------------------------------------


@dataclass(frozen=True)
class Relation:
    name: str
    op: str | None = None
    version: str | None = None

    def satisfied_by(self, version: str | None) -> bool:
        if self.op is None:
            return True
        if version is None:
            return False
        return _OPERATORS[self.op](compare_versions(version, self.version or ""))

    def __str__(self) -> str:
        return self.name if self.op is None else f"{self.name} ({self.op} {self.version})"


_RELATION = re.compile(
    r"""^(?P<name>[a-z0-9][a-z0-9+.\-]*)(?::[a-z0-9\-]+)?
        \s*(?:\(\s*(?P<op><<|<=|=|>=|>>|<|>)\s*(?P<version>[^)\s]+)\s*\))?
        \s*(?:\[[^\]]*\])?\s*(?:<[^>]*>\s*)*$""",
    re.VERBOSE,
)


def parse_relations(value: str) -> list[tuple[Relation, ...]]:
    """Parses a `Depends`-style field into groups of alternatives."""
    groups = []
    for group in value.split(","):
        if not group.strip():
            continue
        alternatives = []
        for alternative in group.split("|"):
            match = _RELATION.match(alternative.strip())
            if not match:
                raise ResolveError(f"Can't parse relation {alternative.strip()!r} in {value!r}")
            op = match["op"]
            # `<` and `>` are deprecated spellings of `<=` and `>=`.
            op = {"<": "<=", ">": ">="}.get(op, op)
            alternatives.append(Relation(match["name"], op, match["version"]))
        groups.append(tuple(alternatives))
    return groups


# --- Repository indices ---------------------------------------------------------------------------


def parse_deb822(text: str) -> Iterator[dict[str, str]]:
    """Yields the stanzas of a `Packages` or `Release` file, with continuation lines joined by `\\n`."""
    stanza: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        if not line.strip():
            if stanza:
                yield stanza
            stanza, key = {}, None
        elif line[0] in " \t":
            if key is not None:
                stanza[key] += "\n" + line.strip()
        else:
            key, _, value = line.partition(":")
            stanza[key] = value.strip()
    if stanza:
        yield stanza


def parse_release_hashes(text: str) -> dict[str, tuple[str, int]]:
    """Returns the `SHA256` table of a `Release` file as `{path: (sha256, size)}`."""
    stanza = next(parse_deb822(text), {})
    hashes = {}
    for line in stanza.get("SHA256", "").splitlines():
        parts = line.split()
        if len(parts) == 3:
            hashes[parts[2]] = (parts[0], int(parts[1]))
    return hashes


def check_suite(release: str, suite: str, url: str) -> None:
    """Fails unless `release` is for `suite`, by the rule of apt's `CheckDist`, which only warns.

    `suite` may be the `Suite` (`stable`) or the `Codename` (`bookworm`), and a path like
    debian-security's old `buster/updates` matches by what comes before its last `/`.
    """
    stanza = next(parse_deb822(release), {})
    names = {stanza.get("Suite"), stanza.get("Codename")} - {None, ""}
    if suite in names or ("/" in suite and suite.rpartition("/")[0] in names):
        return
    raise ResolveError(
        f"{url} is for suite {stanza.get('Suite') or '(none)'} "
        f"(codename {stanza.get('Codename') or '(none)'}), not {suite}"
    )


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    architecture: str
    url: str
    sha256: str
    size: int
    priority: str
    depends: tuple[tuple[Relation, ...], ...]
    provides: tuple[Relation, ...]


def parse_packages(text: str, uri: str) -> list[Package]:
    packages = []
    for stanza in parse_deb822(text):
        depends = [
            *parse_relations(stanza.get("Pre-Depends", "")),
            *parse_relations(stanza.get("Depends", "")),
        ]
        provides = [group[0] for group in parse_relations(stanza.get("Provides", ""))]
        packages.append(
            Package(
                name=stanza["Package"],
                version=stanza["Version"],
                architecture=stanza.get("Architecture", ""),
                url=f"{uri.rstrip('/')}/{stanza['Filename']}",
                sha256=stanza["SHA256"],
                size=int(stanza["Size"]),
                priority=stanza.get("Priority", ""),
                depends=tuple(depends),
                provides=tuple(provides),
            )
        )
    return packages


@dataclass(frozen=True)
class Source:
    uri: str
    suite: str
    components: tuple[str, ...]

    @classmethod
    def parse(cls, line: str) -> Source:
        parts = line.split()
        if len(parts) < 3:
            raise ResolveError(f"Expected `<uri> <suite> <component>...` as a source, got {line!r}")
        return cls(parts[0].rstrip("/"), parts[1], tuple(parts[2:]))


# --- Resolution -----------------------------------------------------------------------------------


class Index:
    """The packages available for one architecture, across all sources."""

    def __init__(self, packages: Iterable[Package]) -> None:
        self.by_name: dict[str, list[Package]] = {}
        self.providers: dict[str, list[tuple[Package, Relation]]] = {}
        seen = set()
        for package in packages:
            # The first source listing a version wins, so the order of sources is a priority.
            if (package.name, package.version) in seen:
                continue
            seen.add((package.name, package.version))
            self.by_name.setdefault(package.name, []).append(package)
            for provided in package.provides:
                self.providers.setdefault(provided.name, []).append((package, provided))

    def candidates(self, relation: Relation) -> list[Package]:
        """Real packages satisfying `relation`, highest version first."""
        matching = [p for p in self.by_name.get(relation.name, ()) if relation.satisfied_by(p.version)]
        return _sort_by_version(matching)

    def provider_candidates(self, relation: Relation) -> list[Package]:
        """Packages providing `relation`, in the order they should be preferred."""
        matching: dict[str, Package] = {}
        for p, provided in self.providers.get(relation.name, ()):
            if not relation.satisfied_by(provided.version):
                continue
            # Keep each provider's highest version; on a tie, the first source's.
            best = matching.get(p.name)
            if best is None or compare_versions(p.version, best.version) > 0:
                matching[p.name] = p
        by_version = _sort_by_version(sorted(matching.values(), key=lambda p: p.name))
        return sorted(by_version, key=lambda p: p.priority != "required")

    def provided_by(self, names: Iterable[str]) -> set[str]:
        """The virtual names provided by any version of the packages named `names`."""
        return {
            provided.name for name in names for p in self.by_name.get(name, ()) for provided in p.provides
        }


def _sort_by_version(packages: list[Package]) -> list[Package]:
    # Stable, so packages with equal versions keep their relative order.
    return sorted(packages, key=functools.cmp_to_key(lambda a, b: compare_versions(b.version, a.version)))


@dataclass(frozen=True)
class Resolved:
    package: Package
    depends: tuple[str, ...]


def resolve(
    index: Index, requested: Iterable[str], exclude: Iterable[str]
) -> tuple[list[str], list[Resolved]]:
    """Resolves the closure of `requested`; returns the root package names and every package."""
    excluded = set(exclude)
    # Excluded packages count as installed, so whatever they provide is installed too.
    installed = excluded | index.provided_by(excluded)
    selected: dict[str, Package] = {}
    depends: dict[str, list[str]] = {}
    parent: dict[str, str | None] = {}
    queue: deque[str] = deque()

    def chain(name: str | None) -> str:
        names = []
        while name is not None:
            names.append(name)
            name = parent[name]
        return " <- ".join(names)

    def selected_satisfier(relation: Relation) -> str | None:
        package = selected.get(relation.name)
        if package is not None and relation.satisfied_by(package.version):
            return package.name
        for package in sorted(selected.values(), key=lambda p: p.name):
            for provided in package.provides:
                if provided.name == relation.name and relation.satisfied_by(provided.version):
                    return package.name
        return None

    def choose(relation: Relation) -> tuple[Package | None, list[Package]]:
        """The package to select for `relation`, and the candidates skipped as version conflicts."""
        conflicts = []
        for candidate in [*index.candidates(relation), *index.provider_candidates(relation)]:
            if candidate.name in excluded:
                continue
            current = selected.get(candidate.name)
            if current is not None and current.version != candidate.version:
                conflicts.append(candidate)
                continue
            return candidate, conflicts
        return None, conflicts

    def select(package: Package, required_by: str | None) -> None:
        if package.name in selected:
            return
        selected[package.name] = package
        parent[package.name] = required_by
        queue.append(package.name)

    roots = []
    for name in sorted(set(requested)):
        if name in installed:
            raise ResolveError(f"{name} is both requested and excluded")
        package = selected.get(name) or choose(Relation(name))[0]
        if package is None:
            raise ResolveError(f"No package named {name}")
        select(package, None)
        roots.append(package.name)

    while queue:
        name = queue.popleft()
        package = selected[name]
        deps = depends.setdefault(name, [])
        for group in package.depends:
            if any(relation.name in installed for relation in group):
                continue
            satisfier = next(filter(None, map(selected_satisfier, group)), None)
            conflicts = []
            if satisfier is None:
                # The first alternative with a candidate that doesn't conflict with the closure wins.
                for relation in group:
                    chosen, skipped = choose(relation)
                    conflicts.extend(skipped)
                    if chosen is not None:
                        select(chosen, name)
                        satisfier = chosen.name
                        break
            if satisfier is None:
                wanted = " | ".join(map(str, group))
                if conflicts:
                    raise ResolveError(
                        f"{wanted} (needed by {chain(name)}) needs "
                        + ", ".join(f"{p.name} {p.version}" for p in conflicts)
                        + ", but the closure already has "
                        + ", ".join(f"{p.name} {selected[p.name].version}" for p in conflicts)
                    )
                raise ResolveError(f"Nothing satisfies {wanted} (needed by {chain(name)})")
            if satisfier != name and satisfier not in deps:
                deps.append(satisfier)

    resolved = [Resolved(selected[n], tuple(sorted(depends[n]))) for n in sorted(selected)]
    return sorted(set(roots)), resolved


# --- Lockfile -------------------------------------------------------------------------------------


def normalize_inputs(
    sources: Iterable[str], architectures: Iterable[str], packages: Iterable[str], exclude: Iterable[str]
) -> dict:
    """The resolve inputs as recorded in a lockfile; the order of sources is kept as a priority."""
    return {
        "sources": [" ".join(line.split()) for line in sources],
        "architectures": sorted(set(architectures)),
        "packages": sorted(set(packages)),
        "exclude": sorted(set(exclude)),
    }


def closures(resolved: Iterable[Resolved]) -> dict[str, list[str]]:
    """The packages each resolved package transitively depends on, apart from itself.

    Apt dependencies can be cyclic, like `libc6` and `libgcc-s1`, so the targets generated from the
    lockfile depend on this closure instead of on each other.
    """
    depends = {r.package.name: r.depends for r in resolved}
    result = {}
    for name in depends:
        seen = {name}
        stack = list(depends[name])
        while stack:
            dep = stack.pop()
            if dep not in seen:
                seen.add(dep)
                stack.extend(depends[dep])
        result[name] = sorted(seen - {name})
    return result


def lockfile(inputs: dict, indices: dict[str, Index]) -> dict:
    architectures = {}
    for arch in inputs["architectures"]:
        try:
            roots, resolved = resolve(indices[arch], inputs["packages"], inputs["exclude"])
        except ResolveError as e:
            raise ResolveError(f"{arch}: {e}") from e
        closure = closures(resolved)
        architectures[arch] = {
            "roots": roots,
            "packages": [
                {
                    "name": r.package.name,
                    "version": r.package.version,
                    "architecture": r.package.architecture,
                    "url": r.package.url,
                    "sha256": r.package.sha256,
                    "size": r.package.size,
                    "depends": list(r.depends),
                    "closure": closure[r.package.name],
                }
                for r in resolved
            ],
        }
    return {"version": LOCKFILE_VERSION, "inputs": inputs, "architectures": architectures}


# --- Fetching -------------------------------------------------------------------------------------


def _fetch(url: str, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except OSError as e:
            not_found = (isinstance(e, urllib.error.HTTPError) and e.code in (404, 410)) or (
                isinstance(e, urllib.error.URLError) and isinstance(e.reason, FileNotFoundError)
            )
            if not_found:
                raise NotFound(f"Failed to fetch {url}: {e}") from e
            # Other client errors won't go away on a retry either.
            client_error = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500
            if client_error or attempt == attempts - 1:
                raise ResolveError(f"Failed to fetch {url}: {e}") from e
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


_INDEX_FORMATS: tuple[tuple[str, Callable[[bytes], bytes]], ...] = (
    (".xz", lzma.decompress),
    (".gz", gzip.decompress),
    ("", lambda data: data),
)


def _decode(data: bytes, url: str) -> str:
    try:
        return data.decode()
    except UnicodeDecodeError as e:
        raise ResolveError(f"{url} isn't UTF-8: {e}") from e


def fetch_release(
    source: Source, keyring: openpgp.Keyring | None, fetch: Callable[[str], bytes] = _fetch
) -> str:
    """Returns the suite's `Release` content, verified against `keyring` unless that's None."""
    dists = f"{source.uri}/dists/{source.suite}"
    if keyring is None:
        url = f"{dists}/Release"
        release = _decode(fetch(url), url)
    else:
        try:
            in_release = fetch(f"{dists}/InRelease")
        except NotFound:
            url = f"{dists}/Release"
            data = fetch(url)
            try:
                openpgp.verify_detached(data, fetch(f"{dists}/Release.gpg"), keyring)
            except openpgp.OpenPGPError as e:
                raise ResolveError(f"{dists}/Release.gpg doesn't verify {url}: {e}") from e
            release = _decode(data, url)
        else:
            url = f"{dists}/InRelease"
            try:
                # Only the signed text counts, so the hashes come from what was verified.
                signed = openpgp.verify_cleartext(in_release, keyring)
            except openpgp.OpenPGPError as e:
                raise ResolveError(f"{url} doesn't verify: {e}") from e
            release = _decode(signed, url)
    # A validly signed `Release` of another suite at this suite's path mustn't pass for it.
    check_suite(release, source.suite, url)
    return release


def fetch_index(
    source: Source, release: str, arch: str, fetch: Callable[[str], bytes] = _fetch
) -> list[Package]:
    """Fetches the `Packages` indices of `source` for `arch`, checked against its `release`."""
    dists = f"{source.uri}/dists/{source.suite}"
    hashes = parse_release_hashes(release)
    packages = []
    for component in source.components:
        for extension, decompress in _INDEX_FORMATS:
            path = f"{component}/binary-{arch}/Packages{extension}"
            if path in hashes:
                break
        else:
            raise ResolveError(f"{dists}/Release lists no Packages index for {component}/binary-{arch}")
        data = fetch(f"{dists}/{path}")
        sha256, size = hashes[path]
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
            raise ResolveError(f"{dists}/{path} doesn't match the SHA256 in {dists}/Release")
        packages.extend(parse_packages(_decode(decompress(data), f"{dists}/{path}"), source.uri))
    return packages


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="JSON file with the normalized inputs.")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--keyring",
        action="append",
        default=[],
        help="An OpenPGP keyring, binary or armored, to verify `Release` signatures with. Repeatable.",
    )
    parser.add_argument("--allow-unsigned", action="store_true", help="Don't verify signatures.")
    args = parser.parse_args(argv)
    if bool(args.keyring) == args.allow_unsigned:
        parser.error("pass either --keyring or --allow-unsigned")

    with open(args.request) as f:
        inputs = json.load(f)
    try:
        keyring = None
        if args.keyring:
            keyrings = []
            for path in args.keyring:
                with open(path, "rb") as f:
                    keyrings.append(f.read())
            try:
                keyring = openpgp.Keyring.load(*keyrings)
            except openpgp.OpenPGPError as e:
                raise ResolveError(f"Can't load {', '.join(args.keyring)}: {e}") from e
        sources = [Source.parse(line) for line in inputs["sources"]]
        releases = [(source, fetch_release(source, keyring)) for source in sources]
        indices = {
            arch: Index(p for source, release in releases for p in fetch_index(source, release, arch))
            for arch in inputs["architectures"]
        }
        result = lockfile(inputs, indices)
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    return 0
