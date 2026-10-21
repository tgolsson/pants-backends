# Distroless backend for Pants

This release targets Pants **2.31.0**. Install it with
`pants-backend-distroless==0.1.0` in `[GLOBAL].plugins` in `pants.toml`, and enable
`pants_backend_distroless` in `[GLOBAL].backend_packages`.

[![PyPI](https://img.shields.io/pypi/v/pants-backend-distroless?label=Latest%20release)](https://pypi.org/project/pants-backend-distroless)

> **Warning**
> This plugin is in development. No stability is guaranteed! Contributions welcome.

This backend builds the parts of distroless-style images that come from Debian packages, in the
spirit of [rules_distroless](https://github.com/GoogleContainerTools/rules_distroless): declare the
top-level packages against an apt repository snapshot, and `pants generate-lockfiles` resolves their
dependencies into a committed lockfile. The locked packages feed `oci_deb_layer` from
[`pants-backend-oci`](https://pypi.org/project/pants-backend-oci), which builds a layer from them
without running `apt`, `dpkg` or maintainer scripts.

## Planned and missing features

* Verifying the GPG signature of the repository's `Release` file.
* Helper layers for `/etc/passwd`, `/etc/group` and a CA bundle.
* Ubuntu packages, which are zstd-compressed.

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

* a `deb_package` target named `<arch>/<package>` for every locked package, which downloads the
  pinned `.deb` and depends on the locked packages it needs, so `:bookworm#amd64/jq` brings `jq`
  and its whole closure;
* a `deb_package_group` target named `<arch>`, depending on all the requested packages, so
  `:bookworm#amd64` is everything.

A layer is for one architecture, so always pick one: `debs=[":bookworm"]` would bring the packages of
every architecture, which `oci_deb_layer` rejects.

| Argument        | Meaning                                                                                       | Default value                                         |
|-----------------|-----------------------------------------------------------------------------------------------|-------------------------------------------------------|
| `name`          | The target name                                                                               | Same as any other target, which is the directory name |
| `resolve`       | The resolve name, a key of `[apt].resolves`.                                                  | Required                                              |
| `sources`       | The repositories, each `<uri> <suite> <component>...` like a sources.list line without `deb`. | Required                                              |
| `architectures` | The Debian architectures to lock, e.g. `amd64` and `arm64`.                                   | Required                                              |
| `packages`      | The top-level packages.                                                                       | Required                                              |
| `exclude`       | Packages treated as already installed, typically those the base image ships.                  | `[]`                                                  |
| `description`   | A description of the target                                                                   |                                                       |
| `tags`          | List of tags                                                                                  | `[]`                                                  |

#### Sources

Pin each URI to a snapshot, such as `https://snapshot.debian.org/archive/debian/<timestamp>`, so
the lockfile can be regenerated and the packages stay downloadable. Several sources can be combined,
for example to add `bookworm-updates`, or `bookworm-security` from
`https://snapshot.debian.org/archive/debian-security/<timestamp>`. Every `Packages` index is checked
against the SHA256 in its suite's `Release` file; the `Release` file's signature is not verified.

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
