# Changelog

## 0.1.0 (unreleased)

- Initial release: `cmake_project` and `cmake_target`, pinned CMake and Ninja, placeholder
  configure with a sandbox-independent configured tree, one cached process per Ninja edge for
  `package`.
- `cmake_test` and the `cmake_tests` generator: tests compiled and linked by Pants as consumers
  of `cmake_target` libraries, with CMake's consumer probes, run from the build directory.
  Parametrizations of a test share its build.
- `CMakeTestWrapperRequest`: run `cmake_test` executables under a wrapper.
- `dependencies` of `cmake_project` and `cmake_test` bring `files`, `cc_sources` and codegen
  producing either; `cmake_test.sources` is optional, and source roots become `-I` flags. A
  path both `sources` and a dependency provide is an error.
- `pants_backend_cmake.generate`: `cpp_generate`, formatted with `[clang-format]`.
