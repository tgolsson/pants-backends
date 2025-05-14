# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
