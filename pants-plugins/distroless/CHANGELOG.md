# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

- Add `deb_packages`, which resolves Debian packages against apt repository snapshots into a
  lockfile with `pants generate-lockfiles`, configured by `[apt].resolves`. It generates a target
  per locked package and architecture for `oci_deb_layer`, which depends on the `.deb` of every
  package in its locked closure, so apt dependency cycles like `libc6` and `libgcc-s1` are fine.
- Ubuntu snapshots from snapshot.ubuntu.com resolve too; the README has an example.
- `generate-lockfiles` verifies the OpenPGP signature of every suite's `InRelease` (or `Release` and
  `Release.gpg`) against the keyrings in the new `keyrings` field of `deb_packages`. **Breaking:**
  `deb_packages` needs either `keyrings` or `allow_unsigned=True`.
