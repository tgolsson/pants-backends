# Changelog

## 0.1.0 (unreleased)

- Initial release: `cmake_project` and `cmake_target` targets, pinned CMake and Ninja tools,
  placeholder-source configure with a sandbox-independent configured tree, Ninja graph
  parser, per-target discovery, one cached process per edge for `package`.
- `cmake_test` and the `cmake_tests` generator: tests compiled and linked by Pants against
  `cmake_target` libraries, run from the build directory for `test`. CMake builds libraries
  only, but decides how a consumer of each is built: configure injects consumer probes, and
  the tests use their compile and link lines (PUBLIC/INTERFACE requirements, transitive link
  closure, CMake's link driver), so nothing PRIVATE leaks.
- `CMakeTestWrapperRequest`: a union for running `cmake_test` executables under a wrapper; a
  wrapper that cannot be set up (`CMakeTestWrapperError`, or a failed process) is a failed
  test. Parametrizations of a test share its build
  (the executable of a parametrized test moves from `<address@params>` to `<address>` under the
  output directory). `files`-like dependencies of a test are generated when needed (codegen to
  `files`/`resources`), so a generating target in `dependencies`, such as `relocated_files` or
  `shell_command`, now runs for the test's build, and a generated translation unit is compiled
  into it.
