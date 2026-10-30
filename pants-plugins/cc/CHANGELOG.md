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
- `DeclaredOutputsFieldSet` and `declared_outputs`: the paths a target will produce, known
  without producing them; `pants_backend_cc.generate` provides them for every target with
  generator `outputs`.
- `find_cc_toolchain`: the toolchain for a platform, or why this host cannot build for it, without
  raising.
