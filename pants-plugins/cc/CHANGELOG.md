# Changelog

## 0.1.0 (unreleased)

- Initial release: `cc_toolchain` and `[cc-toolchains].strict`, the compile-step interface
  (`CompileStepsFieldSet`, `compile_steps`, `CompileInputsRequest`), the header scan
  (`scan_headers`, `HeaderUniverse`), `pants_backend_cc.lint.clang_tidy` and
  `pants_backend_cc.generate` (`cpp_generate`).
- `cc_test` and the `cc_tests` generator under `[cc-test-runner]`: test executables Pants
  compiles and links against libraries from any backend implementing `CcLibraryFieldSet`,
  optionally run under a wrapper (`CcTestWrapperRequest`). `BuiltTest.coverage_notes` holds
  the `.gcno` notes of a coverage build, for coverage tooling.
- `cc_binary`: an executable built like a `cc_test` against the same libraries, which
  `pants package` writes to `output_path`. `cc_test` build errors are now worded for both
  ("executables build for the host", "the executable is compiled as a consumer of it").
- `DeclaredOutputsFieldSet` and `declared_outputs`: the paths a target will produce, known
  without producing them; `pants_backend_cc.generate` provides them for every target with
  generator `outputs`.
- `pants_backend_cc.lint.compile_cost`: `[compile-cost]` under `pants lint`, gcc
  `-ftime-report` user CPU time per compile step under `compile_cost_paths`, as a report, with
  an optional frontend budget that `compile_cost_exempt_paths` are not held to.
- `find_cc_toolchain`: the toolchain for a platform, or why this host cannot build for it, without
  raising.
