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
