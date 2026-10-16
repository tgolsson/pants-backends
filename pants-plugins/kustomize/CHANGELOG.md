# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.7.0 - 2026-10-17

- Target Pants `2.31.0`.
- Require `pants_backend_k8s>=0.7.0`.
- Replace remaining union-dispatched `Get` calls with polymorphic call-by-name rules.

- Default to Kustomize `v5.8.3`; backfill `v5.7.1` and `v5.0.3`, and retain the final
  4.x release, `v4.5.7`, for all four Linux/macOS architectures.
- Add download checks for every recorded version/platform pair.

## 0.6.1 - 2026-10-16

- Replace `pdm-pep517` with `pdm-backend` to produce correctly normalized source archive names for PyPI.
- Require `pants_backend_k8s>=0.6.1`.

## 0.6.0 - 2026-10-16

- Now targets `pants` version `2.30`.
- Migrate rules to the Pants call-by-name API.

## 0.5.0 - 2025-05-14

- Now targets `pants` version `2.24`.

## 0.4.0 - 2024-09-19

- Now targets `pants` version `2.22`.
- Add `tailor` support
- Import `git` to sandbox during expansion to support github downloads
  - submodule check seems to fail so it might help to add `?submodules=false` to the link to disable it

## [0.3.0] - 2023-11-19

- Target pants version is now 2.18.0

## [0.2.0] - 2023-06-18

* Target pants version is now 2.16.0, with support for 2.15.0.

## [0.1.2] - 2022-12-06

* Add support for specializing how other packages are injected into Kustomize files.

## [0.1.1] - 2022-11-17

* Move to new repository

## [0.1.0] - 2022-11-13

Initial release.
