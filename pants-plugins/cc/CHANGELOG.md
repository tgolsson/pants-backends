# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - Unreleased

- Target Pants `2.32.x`.
- Require `pants_backend_platforms>=0.1.0`.
- Require `pants_backend_generate>=0.1.0` through the `generate` extra.
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
    reports them with gcovr (`[gcovr]`) under `dist/coverage/cc/`. Whoever compiles an object
    names the directory it did so in next to the object's notes
    (`pants_backend_cc.coverage.data.compile_directories`), and the gcov gcovr runs runs gcov
    there, once per object.
