# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.6.1 - 2026-10-16

- Replace `pdm-pep517` with `pdm-backend` to produce correctly normalized source archive names for PyPI.

## 0.6.0 - 2026-10-16

- Now targets `pants` version `2.30`.
- Migrate rules to the Pants call-by-name API.
- Fix Twine PEX creation after the call-by-name migration.
- Support `skip_twine` and distribution `output_path` on `python_distribution_with_secret` targets.

## 0.5.0 - 2025-05-14

- Now targets `pants` version `2.24`.

## 0.4.0 - 2024-09-19

- Now targets `pants` version `2.22`.

## [0.3.0] 2023-11-21

* Target pants version is now 2.18.0, with support for 2.17.0.

## [0.2.0] - 2023-06-18

* Target pants version is now 2.16.0, with support for 2.15.0.

## 0.1.0 - 2023-02-10

* Initial release
