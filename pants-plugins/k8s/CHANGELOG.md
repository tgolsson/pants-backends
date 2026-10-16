# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## Unreleased

- Default to kubectl `v1.37.1`; include the latest patches of maintained minors
  `v1.36.5`, `v1.35.9`, and `v1.34.12`, and remove EOL `v1.25.2`.
- Include Linux ARM64 binaries alongside Linux x86-64 and both macOS architectures.
- Verify every recorded version/platform download, including macOS checksums that
  were incorrect in the previous table.
- Track Kubernetes EOL dates when refreshing the table; 1.34 reaches EOL on 2026-10-27.

## 0.6.1 - 2026-10-16

- Replace `pdm-pep517` with `pdm-backend` to produce correctly normalized source archive names for PyPI.

## 0.6.0 - 2026-10-16

- Now targets `pants` version `2.30`.
- Migrate rules to the Pants call-by-name API.

## 0.5.0 - 2025-05-14

- Now targets `pants` version `2.24`.

## 0.4.0 - 2024-09-19

- Now targets `pants` version `2.22`.
- Kubeconfig files are no longer automatically picked up from the host. In order to support scripted
  provisioning and local configuration files, all targets now take a `kubeconfig` field. This can
  point to either `kubeconfig` target which uses a straight source or a generated, or a
  `host_kubeconfig` target which will attempt to load from `~/.kube/config`. Both of these also
  allow you to specify default namespaces, contexts, clusters and users. All these fields can now
  also be specified on the object, whereas only cluster/context could before.

## [0.3.0] - 2023-11-19

- Target pants version is now 2.18.0

## [0.2.0] - 2023-06-18

* Target pants version is now 2.16.0, with support for 2.15.0.

## [0.1.1] - 2022-11-17

* Move to new repository

## [0.1.0] - 2022-11-12

* Initial release.
