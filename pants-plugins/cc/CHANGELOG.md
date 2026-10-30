# Changelog

## 0.1.0 (unreleased)

- Initial release:
  - `cc_toolchain` and `[cc-toolchains].strict`; `find_cc_toolchain` gives the toolchain for a
    platform, or why this host cannot build for it, without raising.
  - The compile-step interface (`CompileStepsFieldSet`, `compile_steps`, `CompileInputsRequest`)
    and the header scan (`scan_headers`, `HeaderUniverse`).
  - `cc_test`, the `cc_tests` generator and `[cc-test-runner]`: executables Pants compiles and
    links against libraries from any backend implementing `CcLibraryFieldSet`, optionally run
    under a wrapper (`CcTestWrapperRequest`).
  - `cc_binary`: an executable built the same way, which `pants package` writes to
    `output_path`.
  - `DeclaredOutputsFieldSet` and `declared_outputs`: the paths a target will produce, known
    without producing them.
  - `pants_backend_cc.lint.clang_tidy`: `[clang-tidy]` under `pants lint`.
  - `pants_backend_cc.generate`: `cpp_generate`.
  - `pants_backend_cc.coverage`: `pants test --use-coverage` builds `cc_test`s against their
    libraries' instrumented builds, sums the `.gcda` files each run writes with gcov-tool and
    reports them with gcovr (`[gcovr]`) under `dist/coverage/cc/`.
