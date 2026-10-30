# pants-backend-cc

A [Pants](https://www.pantsbuild.org) backend for the C and C++ pieces that build-system backends
share: toolchains, interfaces for their compiles and libraries, and the tools built on them.

**Status: alpha.** Works with Pants 2.31 and 2.33.

## Toolchains

```toml
backend_packages = ["pants_backend_platforms", "pants_backend_cc"]
```

```python
platform(name="linux-x86_64", os="linux", arch="x86_64")
platform(name="windows-x86_64", os="windows", arch="x86_64")

cc_toolchain(name="linux", host="linux-x86_64", target="linux-x86_64", cc="gcc", cxx="g++", ar="ar")
cc_toolchain(
    name="linux-to-windows",
    host="linux-x86_64",
    target="windows-x86_64",
    cc="x86_64-w64-mingw32-gcc",
    cxx="x86_64-w64-mingw32-g++",
    ar="x86_64-w64-mingw32-ar",
)
```

`host` and `target` are [pants-backend-platforms](../platforms) `platform` targets. The compilers'
`--version` output and the contents of `ar` and of the linker the compiler drives are part of the
cache key; headers and libraries are not. A native pair without a `cc_toolchain` uses `gcc`, `g++`
and `ar`, which `[cc-toolchains].strict` forbids.

## Compile steps, for backend authors

- `resolve_cc_toolchain(CcToolchainRequest(platform))` returns the toolchain for the host and that
  platform. Put `CcToolchain.env` in the process environment.
- A backend exposes its compiles by making a field set a `CompileStepsFieldSet` member and
  registering `UnionRule(CompileStepsFieldSet, MyFieldSet)`. Its rule takes the field set and a
  `TargetPlatform` and returns `CompileSteps`, one `CompileStep` per translation unit: the
  source and object (relative to the sandbox root), the language, the complete command
  (compiler first) with the directory it runs in, and its environment.
- A step's `inputs` is a member of the `CompileInputsRequest` union whose rule returns
  `CompileInputs`, the sandbox the command runs in. Listing steps builds no sandbox; a consumer
  calls `compile_inputs` for the steps it runs.
- `pants_backend_cc.util_rules.scan` runs a compile with `-M -MG` to find the headers a
  translation unit reads, against a `HeaderUniverse` the backend provides.
- A library backend makes a field set a `CcLibraryFieldSet` member and implements its three
  rules, `library_cwd`, `compile_interface` and `link_interface`; `util_rules/library.py`
  documents them.

## Tests

`cc_test` compiles `sources` into an executable linked against the libraries in
`dependencies`, from any backend that provides them, and `pants test` runs it with `args`;
`cc_tests` generates one per directory of translation units. `pants help cc_test` describes the
fields. A test that fails to build is a failed test.

- A library may require the directory its consumers compile and link in; a test whose
  libraries require different ones fails.
- Every library's flags and link arguments are concatenated in declared order, without
  deduplication, so objects or whole-archive spans that several libraries bring are repeated
  and may define a symbol twice.
- Shared libraries are found at run time through an `$ORIGIN`-relative run path
  (`@loader_path` on macOS).
- Test executables are built under their address without parameters, so parametrizing how a
  test runs shares its build.
- A compile whose flags ask for coverage (`--coverage`) keeps its `.gcno` notes, in
  `BuiltTest.coverage_notes`.
- Another backend can run tests under a wrapper (an emulator, a tracer) through the
  `CcTestWrapperRequest` union; `goals/wrapper.py` documents the contract.
- `[cc-test-runner].skip` skips the tests.

## Binaries

`cc_binary` builds an executable for the host the way `cc_test` builds a test, from the same
fields less the ones for running it, and `pants package` writes it to `output_path`. A library's
working directory and the declared link order apply as for tests. Only the executable is
written, not the shared libraries it links against, and its run path (`$ORIGIN`-relative, or
`@loader_path` on macOS) points at where they sit in the build sandbox, so from `dist/` it will
not find them: link static libraries into a binary you package.

## clang-tidy

The optional sub-backend `pants_backend_cc.lint.clang_tidy` adds `[clang-tidy]` to `pants lint`,
from the `clang-tidy` PyPI wheel (a pex, hence `pants.backend.python`). A target with
`clang_tidy_paths` (a field the providing backend registers) has its compile steps listed for the
host, and clang-tidy runs once per step whose source is under those directories, in that step's
sandbox and with its command.

- A provider's sandbox may hold only the headers a gcc dependency scan found, so an include
  that only clang takes (behind `__clang__` or `__has_include`) is missing.
- Configuration comes from `.clang-tidy` files in the directories above each translation unit; a
  `--config-file` in `args` is not in the sandbox.
- The default lockfile has no linux-aarch64 wheel.

```toml
backend_packages = [
  "pants.backend.python",
  "pants_backend_platforms",
  "pants_backend_cc",
  "pants_backend_cc.lint.clang_tidy",
  "pants_backend_cmake",
]
```

## `cpp_generate`

The optional sub-backend `pants_backend_cc.generate` provides `cpp_generate`, the C/C++
specialization of `pants-backend-generate`: C/C++ sources written by a generator, formatted with
Pants' `pants.backend.cc` `[clang-format]` (a pex, hence `pants.backend.python`;
`[clang-format].skip` is not read). `pants generate` writes them, `pants lint` checks the
committed copies, and a target depending on one compiles them. Install
`pants_backend_cc[generate]` (it pulls in `pants-backend-generate`) and enable:

```toml
backend_packages = [
  "pants.backend.python",
  "pants_backend_generate",
  "pants_backend_cc",
  "pants_backend_cc.generate",
]
```
