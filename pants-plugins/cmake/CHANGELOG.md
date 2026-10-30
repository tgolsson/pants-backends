# Changelog

## 0.1.0 (unreleased)

- Initial release:
  - `cmake_project`, a target generator: a `cmake_target` per library and executable, from a
    placeholder-only configure per platform the project targets and this host can build for,
    with `overrides`. The generated targets depend on `<project>#_inputs`
    (`cmake_project_inputs`), which owns the project's files, dependencies and compile steps.
  - Pinned CMake and Ninja; a configured tree independent of the sandbox it was configured in;
    one cached process per Ninja edge for `package`.
  - Static and shared `cmake_target`s are pants-backend-cc libraries, read from CMake's
    consumer probes, for `cc_test` and `cc_binary`.
  - `dependencies` of `cmake_project` bring `files`, `cc_sources` and codegen producing either.
  - pants-backend-cc compile steps, one per compile edge, and `clang_tidy_paths` for
    `pants_backend_cc.lint.clang_tidy`.
  - Platforms through pants-backend-platforms: compilers from `cc_toolchain`,
    `cmake_toolchain_file`, and `platforms` on `cmake_project` and `cmake_target`.
  - `coverage_cmake_args`: the arguments a coverage consumer's own configure of the project
    adds to `cmake_args`.
