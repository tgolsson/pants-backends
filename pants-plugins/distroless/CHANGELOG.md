# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Breaking

- `deb_packages` generates targets only for the requested `packages`, from the BUILD file: a
  `deb_package` per architecture and package, whose sources are the `.deb` files of its locked
  closure, and the `deb_package_group` per architecture. The `deb_package_file` targets
  (`<arch>/<package>/deb`) and the targets for unrequested packages in a closure are gone; add a
  package to `packages` to depend on it directly.
- The lockfile format is version 3, which records the package each requested name resolved to.
  Regenerate lockfiles with `pants generate-lockfiles`.

### Changed

- Target generation no longer reads the lockfile, so a missing or out-of-date lockfile no longer
  breaks `pants list ::` and the like, and `generate-lockfiles` doesn't depend on it. Building
  anything from the packages fails with an error naming the lockfile and the `generate-lockfiles`
  command, instead of a warning followed by an error that the package's target was not generated.

## 0.1.0rc0 - 2026-11-04

- Target Pants `2.32.x`.

First release. It builds the Debian and Ubuntu package side of distroless-style images, for
`oci_deb_layer` from `pants-backend-oci`.

- `deb_packages` resolves Debian packages against apt repository snapshots into a lockfile with
  `pants generate-lockfiles`, configured by `[apt].resolves`. It generates a target per locked
  package and architecture for `oci_deb_layer`, which depends on the `.deb` of every package in
  its locked closure, so apt dependency cycles like `libc6` and `libgcc-s1` are fine.
- Ubuntu snapshots from snapshot.ubuntu.com resolve too; the README has an example.
- `generate-lockfiles` verifies the OpenPGP signature of every suite's `InRelease` (or `Release`
  and `Release.gpg`) against the keyrings in the `keyrings` field of `deb_packages`.
  `deb_packages` needs either `keyrings` or `allow_unsigned=True`.
- `generate-lockfiles` fails unless each suite's `Release` names it as its `Suite` or `Codename`,
  by apt's rule (where apt only warns).
- The resolver runs from `apt-resolver`, a scie with its own pinned Python, so a resolve's cache
  key names no host path and its result can be shared across checkouts and through a remote
  cache. The Python is set by `[apt].resolver_python_version` and `[apt].resolver_pbs_release`.
- The README covers moving from a Python base image: the `/usr/bin/python3.X` pex shebang and
  pex's harmless `ldd` warning.
