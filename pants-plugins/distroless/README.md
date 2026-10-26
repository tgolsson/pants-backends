# Distroless backend for Pants

This release targets Pants **2.31.0**. Install it with
`pants-backend-distroless==0.1.0` in `[GLOBAL].plugins` in `pants.toml`, and enable
`pants_backend_distroless` in `[GLOBAL].backend_packages`.

[![PyPI](https://img.shields.io/pypi/v/pants-backend-distroless?label=Latest%20release)](https://pypi.org/project/pants-backend-distroless)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This backend builds the parts of distroless-style images that come from Debian or Ubuntu packages, in the
spirit of [rules_distroless](https://github.com/GoogleContainerTools/rules_distroless): declare the
top-level packages against an apt repository snapshot, and `pants generate-lockfiles` resolves their
dependencies into a committed lockfile. The locked packages feed `oci_deb_layer` from
[`pants-backend-oci`](https://pypi.org/project/pants-backend-oci), which builds a layer from them
without running `apt`, `dpkg` or maintainer scripts. `pants-backend-oci` also has the helper layers such
an image needs: `oci_passwd_layer`, `oci_group_layer`, `oci_cacerts_layer` and `oci_locale_layer`.

## Targets

### `deb_packages`

A set of Debian packages resolved into a lockfile. Each resolve is named in `[apt].resolves`, which
maps it to its lockfile, and is declared by exactly one `deb_packages` target.

``` toml
[apt.resolves]
bookworm = "3rdparty/debian/bookworm.lock.json"
```

``` python
deb_packages(
    name="bookworm",
    resolve="bookworm",
    sources=["https://snapshot.debian.org/archive/debian/20241001T000000Z bookworm main"],
    architectures=["amd64", "arm64"],
    packages=["jq"],
    # Already in the base image.
    exclude=["libc6"],
    # From the `debian-archive-keyring` package.
    keyrings=["debian-archive-keyring.gpg"],
)

oci_deb_layer(
    name="jq",
    debs=[":bookworm#amd64/jq"],
)
```

Run `pants generate-lockfiles --resolve=bookworm` to write the lockfile, and again whenever the
target changes. Until then, and while the lockfile is out of date, Pants warns and generates no
packages, so anything using them fails with an error that `:bookworm#amd64/jq` was not generated.
The warning is only printed when the targets are generated, so with `pantsd` a later run may show
just that error; regenerating the lockfile fixes it.

The target generates:

* a `deb_package` target named `<arch>/<package>` for every locked package, which depends on the
  pinned `.deb` of the package and of every locked package in its closure, so `:bookworm#amd64/jq`
  brings `jq` and its whole closure;
* a `deb_package_file` target named `<arch>/<package>/deb` for every locked package, which downloads
  only its pinned `.deb`;
* a `deb_package_group` target named `<arch>`, depending on all the requested packages, so
  `:bookworm#amd64` is everything.

Packages don't depend on each other's targets, only on `.deb` files, and the lockfile records each
package's closure. Apt dependency cycles are therefore fine: `libc6` and `libgcc-s1` depend on each
other in Debian, and `:bookworm#amd64/libc6` brings both.

A layer is for one architecture, so always pick one: `debs=[":bookworm"]` would bring the packages of
every architecture, which `oci_deb_layer` rejects.

| Argument         | Meaning                                                                                       | Default value                                         |
|------------------|-----------------------------------------------------------------------------------------------|-------------------------------------------------------|
| `name`           | The target name                                                                               | Same as any other target, which is the directory name |
| `resolve`        | The resolve name, a key of `[apt].resolves`.                                                  | Required                                              |
| `sources`        | The repositories, each `<uri> <suite> <component>...` like a sources.list line without `deb`. | Required                                              |
| `architectures`  | The Debian architectures to lock, e.g. `amd64` and `arm64`.                                   | Required                                              |
| `packages`       | The top-level packages.                                                                       | Required                                              |
| `exclude`        | Packages treated as already installed, typically those the base image ships.                  | `[]`                                                  |
| `keyrings`       | OpenPGP keyrings (`.gpg` or `.asc`) to verify the repositories' `Release` signatures with.    | Required unless `allow_unsigned=True`                 |
| `allow_unsigned` | Resolve without verifying signatures. Can't be combined with `keyrings`.                      | `False`                                               |
| `description`    | A description of the target                                                                   |                                                       |
| `tags`           | List of tags                                                                                  | `[]`                                                  |

#### Sources

Pin each URI to a snapshot, such as `https://snapshot.debian.org/archive/debian/<timestamp>`, so
the lockfile can be regenerated and the packages stay downloadable. Several sources can be combined,
for example to add `bookworm-updates`, or `bookworm-security` from
`https://snapshot.debian.org/archive/debian-security/<timestamp>`. Every `Packages` index is checked
against the SHA256 in its suite's signed `Release` file, see [Signature verification](#signature-verification).

Ubuntu works the same way, from [snapshot.ubuntu.com](https://snapshot.ubuntu.com). Its updates
and security fixes are separate suites of the same archive, and unlike the live archive, where
arm64 lives on `ports.ubuntu.com`, the snapshot serves arm64 packages under `/ubuntu` too, so one
set of sources covers both architectures. `Phased-Update-Percentage` is ignored: a snapshot lock
takes a phased update like any other version. Ubuntu's packages are zstd-compressed, which
`oci_deb_layer` reads with its `zstd` tool.

``` python
deb_packages(
    name="noble",
    resolve="noble",
    sources=[
        "https://snapshot.ubuntu.com/ubuntu/20241001T000000Z noble main",
        "https://snapshot.ubuntu.com/ubuntu/20241001T000000Z noble-updates main",
        "https://snapshot.ubuntu.com/ubuntu/20241001T000000Z noble-security main",
    ],
    architectures=["amd64", "arm64"],
    packages=["jq", "ca-certificates"],
    # Already in the ubuntu:noble base image.
    exclude=["libc6", "debconf", "libssl3t64"],
    # From the `ubuntu-keyring` package.
    keyrings=["ubuntu-archive-keyring.gpg"],
)
```

#### Signature verification

Like rules_distroless's `gpg_keys`, `generate-lockfiles` verifies every suite's `InRelease`, or its
`Release` and `Release.gpg` when there's no `InRelease`, against `keyrings`, and fails unless at
least one signature is good and made by a key in them. A bad signature from one of their keys fails
too. The hashes of the `Packages` indices are only taken from the verified file. It also fails
unless the signed file's `Suite` or `Codename` is the suite asked for, so another suite's validly
signed `Release` can't stand in for it. This is apt's rule, though apt only warns: a path like
debian-security's old `buster/updates` matches by what comes before its last `/`. Either `keyrings` or `allow_unsigned=True` must be set.

Use the archive's own keyring, from the snapshot you resolve against so it's pinned too:
`/usr/share/keyrings/debian-archive-keyring.gpg` from the `debian-archive-keyring` package, or
`/usr/share/keyrings/ubuntu-archive-keyring.gpg` from `ubuntu-keyring`. Copy it next to the
`deb_packages` target, since sources can't come from another directory.

Verification needs no `gpgv` or `sqv`: it's built into the resolver, and supports what Debian and
Ubuntu sign with, v4 RSA and Ed25519 keys with SHA-2 hashes. Older snapshots signed only with SHA-1
need `allow_unsigned=True`, which turns signature verification off entirely; the suite is still
checked. Every key in the keyrings is trusted as given: expiry and revocation aren't checked, since a
snapshot's `Release` files are historical. For the same reason, freshness (`Valid-Until`) isn't
checked either: the snapshot URL is what pins it. The keys aren't recorded in the lockfile, so
changing `keyrings` doesn't make it out of date.

#### Resolution

The `Pre-Depends` and `Depends` of the requested packages are resolved transitively, for each
architecture separately, much like rules_distroless does:

* A package resolves to its highest version, across all sources, that satisfies the constraint.
  When sources have the same version, the first source wins.
* For alternatives (`a | b`), a package already in the closure is preferred; otherwise the first
  alternative that exists wins.
* A virtual package (`Provides`) resolves to a provider already in the closure, else the only
  provider, else a `Priority: required` one, else the highest version, else the first by name.
* The result doesn't depend on the order of `packages`.

`Recommends` and `Suggests` are not installed, and `Conflicts` and `Breaks` are not checked.

#### Packages already in the base image

A layer replaces any file the base image has at the same path, and nothing warns about it. If a
locked package is also in the base, its files from the snapshot overwrite the base's copies, which
can mix versions when the base image was built from a different archive state than the snapshot.
List such packages in `exclude`: they count as installed, satisfy any dependency on them, and are not
traversed, so whatever only they need is left out as well. Images built on an empty base leave
`exclude` empty.

## Moving from a Python base image

A few things change when a Python app moves from a pulled base such as `python:3.11-slim` to one
built from Debian or Ubuntu packages.

**The interpreter is `/usr/bin/python3.X`.** The official Python images install it under
`/usr/local/bin` and have `/usr/bin/env`. A package's interpreter is `/usr/bin/python3.X`, where
`3.X` is the suite's Python: 3.11 in Debian bookworm, 3.13 in trixie, 3.12 in Ubuntu noble. Build
the pex for that version, since its interpreter constraints and wheels must match. An image built
from packages has no `env` unless it locks `coreutils`, and a pex's default shebang is
`#!/usr/bin/env python3.X`, so the pex fails to start. Point the shebang at the interpreter, and
make the pex the entrypoint with `oci_image_build`, whose first package is the entrypoint:

``` python
pex_binary(
    name="app",
    entry_point="app.py:main",
    shebang="/usr/bin/python3.11",
)

oci_image_build(
    name="image",
    base=[...],
    layers=[":python"],
    packages=[":app"],
)
```

`oci_python_image` doesn't use the shebang: it runs the pex as `python <pex>`, and no Debian or
Ubuntu package but `python-is-python3` provides `/usr/bin/python`. Lock that package to keep using
`oci_python_image`. `python3-minimal`, which `python3` pre-depends on, adds `/usr/bin/python3`.

**pex warns that `ldd` is missing.** On startup a pex may print `Failed to detect if this
interpreter is running under musl libc: [Errno 2] No such file or directory: 'ldd'`. pex runs `ldd`
to tell musl from glibc and, when it can't, assumes glibc, which is right for Debian and Ubuntu.
The warning is harmless. To hide it, set `emit_warnings=False` on the `pex_binary`, or
`PEX_EMIT_WARNINGS=false` in the image's environment; either hides all of pex's warnings. Locking
`libc-bin` doesn't help in an image without a shell, since Debian's `ldd` is a bash script.

**The Pants store grows quickly.** Rebuilding multi-GB images adds new layers to the Pants store on
every change. See
[Pants store size](https://github.com/tgolsson/pants-backends/tree/main/pants-plugins/oci#pants-store-size)
in the OCI README for a setting that keeps it in check.
