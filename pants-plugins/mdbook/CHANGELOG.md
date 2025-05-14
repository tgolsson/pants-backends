# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## 0.6.0 - 2025-05-14

- Now targets `pants` version `2.24`.

## 0.5.0 - 2024-09-19

- Now targets `pants` version `2.22`.

## [0.4.1] - 2024-04-12

- Will now use x86_64 mdbook binary on arm64, relying on Rosetta.

## [0.4.0] - 2024-04-05

- Will now also include codegen sources, allowing integration with `adhoc_tool` and other generators. Note that only file and direct mdbook sources are included.

## [0.3.0] - 2023-11-21

* Target pants version is now 2.18.0, with support for 2.17.0.
* Updated mdbook version to 0.4.35
* Now includes support for arm64 on Linux

## [0.2.0] - 2023-06-18

* Target pants version is now 2.16.0, with support for 2.15.0.

## [0.1.3] - 2022-11-18

* Raise error if `book.toml` is not found
* Handle all sources in dependencies

## [0.1.2] - 2022-11-17

* Merge with all other backends
* Fix README links

## [0.1.1] - 2022-11-12

* Fix README.

## [0.1.0] - 2022-11-12

* Initial release.
