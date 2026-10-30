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
  sandbox, and has `clang_tidy_paths` for `pants_backend_cc.lint.clang_tidy` and
  `compile_cost_paths` and `compile_cost_exempt_paths` for `pants_backend_cc.lint.compile_cost`.
- Platforms through pants-backend-platforms: compilers from `cc_toolchain` (pants-backend-cc),
  `cmake_toolchain_file`, and `platforms` on `cmake_target`.
- `cmake_project` is a target generator: a `cmake_target` per library and executable, from a
  placeholder-only configure per platform the project targets and this host can build for,
  with `overrides`. A setup mistake, or a platform that fails to configure, is logged and
  generates none. The generated targets depend on the new `cmake_project_inputs` target
  `<project>#_inputs`, which owns the project's files and dependencies and carries its compile
  steps and `clang_tidy_paths`; they get no inferred dependency on the project. A CMake target
  named `_inputs` is an error. **Breaking:**
  - `build_type` and `platforms` on a project apply to its generated targets;
  - a hand-written `cmake_target` configures with its own `build_type`, else with the one its
    `project` gives the targets it generates; a parametrized project is named as
    `:proj@build_type=X` (a `parametrize`d `cmake_target` never resolved to the matching
    project);
  - compile steps (clang-tidy) belong to `<project>#_inputs`, configured with its `build_type`.
- `coverage_cmake_args` on `cmake_project` replaces the bool `coverage`. **Breaking:** a project
  no longer captures `.gcno` in its usual build; a coverage consumer (`pants_backend_cc.coverage`)
  gets a configure of its own with these arguments added to `cmake_args`, whose replay captures
  them, and the link interface gives it the notes and compile inputs behind its libraries.
