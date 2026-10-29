# Changelog

## 0.1.0 (unreleased)

- Initial release: `cmake_project` and `cmake_target`, pinned CMake and Ninja, placeholder
  configure with a sandbox-independent configured tree, one cached process per Ninja edge for
  `package`.
- Static and shared `cmake_target`s are pants-backend-cc libraries, read from CMake's
  consumer probes, for `cc_test`.
- `dependencies` of `cmake_project` bring `files`, `cc_sources` and codegen producing either. A
  path both `sources` and a dependency provide is an error.
- `cmake_project` provides pants-backend-cc compile steps, one per compile edge in the edge's
  sandbox, and has `clang_tidy_paths` for `pants_backend_cc.lint.clang_tidy`.
- Platforms through pants-backend-platforms: compilers from `cc_toolchain` (pants-backend-cc),
  `cmake_toolchain_file`, and `platforms` on `cmake_target`.
