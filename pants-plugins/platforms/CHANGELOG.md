# Changelog

## 0.1.0 (unreleased)

- Initial release: the `platform` target, the `platforms` field, `[platforms].enabled` and
  `[platforms].host`, and the `build_for_platform` contract with `package_for_platforms`.
- `targets_of_alias`: the targets of one type, read without `AllTargets`, so rules that run
  during target generation can use it; platform lookup uses it.
