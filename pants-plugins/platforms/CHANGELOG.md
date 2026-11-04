# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - Unreleased

- Target Pants `2.32.x`.
- Initial release: the `platform` target, the `platforms` field, `[platforms].enabled` and
  `[platforms].host`, the `build_for_platform` contract with `package_for_platforms`, and
  `targets_of_alias`, the targets of one type read without `AllTargets`, so that rules running
  during target generation can look platforms up.
