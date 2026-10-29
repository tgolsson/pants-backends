# pants-backend-cmake

A [Pants](https://www.pantsbuild.org) backend for CMake projects. CMake stays the source of truth:
the backend runs `cmake -G Ninja` once, reads the generated `build.ninja`, and replays every edge
(one translation unit, one archive, one link) as its own cached Pants process. Nothing in
`CMakeLists.txt` is declared twice, and the unit of caching is the edge, not the project.
CMake provides the build artefacts; tests, packaging and dependencies are Pants' business.

**Status: alpha.** Linux hosts, cross builds through a `cmake_toolchain_file`, Ninja generator,
local execution only (the compilers are found on the machine).

## Targets

- `cmake_project`: the project. `sources` is what CMake reads: CMake files, configure-time
  inputs, and any source or header no dependency provides. Files matching
  `configure_placeholders` (sources and headers by default) reach configure as zero-byte
  placeholders, so editing one never reruns configure; everything else is given as is.
  `dependencies` are `files`, `cc_sources` and codegen targets producing either (such as
  `cpp_generate`); their files are mounted for the compile and link steps, and configure sees
  only those matching `configure_placeholders`. A path both `sources` and a dependency
  provide is an error (drop it with `"!gen/registry.cpp"`). `cmake_args`, `build_dir`
  (default `build`) and `build_type` are passed to CMake.
- `cmake_target`: one CMake `STATIC`, `SHARED`, `MODULE` library or `EXECUTABLE`, named by
  `project` (the `cmake_project`'s address, inferred as a dependency) and `cmake_name`
  (defaults to the target name). `pants package` writes its outputs at their build paths,
  `dist/<build_dir>/libfoo.a`, and those for other `platforms` under `dist/<platform>/` (see
  Platforms). Object libraries are internal and are not addressed.
- `cmake_test`: a test executable **Pants** compiles from `sources` (optional when
  dependencies provide the translation units) and links against the `cmake_target` libraries
  in `dependencies`, in the order written, as CMake would build a consumer of them (see How
  it works); nothing PRIVATE leaks. It adds `include_dirs`, `-I<root>` for every source root
  owning one of its files, `defines`, `compiler_args` and `linker_args`, and runs with `args`
  from the libraries' build directory. Translation units of direct `files`, `cc_sources` or
  codegen dependencies are compiled in (a vendored framework, a generated runner; never a
  library's own sources), and their other files are staged for the run with
  `runtime_package_dependencies`. A test that fails to build is a failed test.
- `cmake_tests`: a generator of one `cmake_test` per directory with translation units under
  `sources` (`tests:cc#unit/ops/abs`); `overrides={"c_abi": {...}}` adjusts single ones.

Another backend can run tests under a wrapper (an emulator, a tracer) through the
`CMakeTestWrapperRequest` union; `goals/wrapper.py` documents the contract. Test executables
are built under their address without parameters, so `parametrize` over a wrapper's field runs
one build several ways.

## `cpp_generate`

The optional sub-backend `pants_backend_cmake.generate` provides `cpp_generate`, the C/C++
specialization of `pants-backend-generate`: C/C++ sources written by a generator, formatted
with the cc backend's `[clang-format]` (a pex, hence `pants.backend.python`;
`[clang-format].skip` is not read). `pants generate` writes them, `pants lint` checks the
committed copies, and a `cmake_project` or `cmake_test` depending on one compiles them.
Install `pants_backend_cmake[generate]` (it pulls in `pants-backend-generate`) and enable:

```toml
backend_packages = [
  "pants.backend.python",
  "pants_backend_generate",
  "pants_backend_cmake",
  "pants_backend_cmake.generate",
]
```

## clang-tidy

The optional sub-backend `pants_backend_cmake.lint.clang_tidy` adds `[clang-tidy]` to `pants
lint`, from the `clang-tidy` PyPI wheel (a pex, hence `pants.backend.python`). A
`cmake_project` with `clang_tidy_paths` is configured for the host and clang-tidy runs once per
compile edge under them, in the sandbox that edge compiles in and with its command.

- The sandbox holds only the headers gcc's dependency scan found, so an include that only clang
  takes (behind `__clang__` or `__has_include`) is missing.
- Configuration comes from `.clang-tidy` files in the directories above each translation unit; a
  `--config-file` in `args` is not in the sandbox.
- The default lockfile has no linux-aarch64 wheel.

```toml
backend_packages = [
  "pants.backend.python",
  "pants_backend_cmake",
  "pants_backend_cmake.lint.clang_tidy",
]
```

## Platforms

The compilers and `ar` come from the `cc_toolchain` ([pants-backend-platforms](../platforms))
for the host and the platform being built; without any, `gcc`, `g++` and `ar` for the host. A
`cmake_target` builds for its `platforms`, a `cmake_test` for the host. Enable
`pants_backend_platforms` to declare `platform` and `cc_toolchain` targets.

The backend adds `cmake_toolchain_file` to `cc_toolchain`, passed as `CMAKE_TOOLCHAIN_FILE`: a
cross build needs one for `CMAKE_SYSTEM_NAME`. See the field's help for what it may contain.

## Subsystems

- `[cmake]`: the pinned CMake download (`version`, `known_versions`).
- `[ninja]`: the pinned Ninja download. Ninja is only needed by configure; the replay never
  runs it.
- `[cmake-test-runner]`: `skip`.
- `[clang-tidy]`: `skip`, `args`, `drop_compiler_args`, `install_from_resolve`.

## How it works

1. **Configure**: `cmake -S <project> -B <build_dir> -G Ninja` in a sandbox, with the
   consumer probes injected through `CMAKE_PROJECT_INCLUDE`. The generated files are kept,
   except the cache, the compiler-id artefacts, the logs and the probe sources; every absolute
   sandbox path in them is rewritten to a placeholder, the paths of CMake's own binaries
   (read from `CMakeCache.txt`) to bare names, and the regenerate edge and its
   "missing input is not an error" twin are dropped, so the configured tree has the same
   digest in every sandbox.
2. **Discovery**: the Ninja graph is parsed; CMake's `# ... build statements for <TYPE> target
   <name>` comments give each edge its target and kind; a target's outputs are what its link
   and symlink edges write; the edges one target needs are found by walking producers and
   stopping at other targets' outputs.
3. **Replay** (`pants package`): one rule per edge, awaiting its producers, each a process with
   exactly its own inputs: the source, the headers it includes (from a per-TU `-M` scan), the
   configure-time generated files, and the outputs of the producers of its declared inputs.
   The engine schedules the dataflow and memoizes shared edges.
4. **Test** (`pants test`): configure injects a *consumer probe* per library and language, a
   hidden executable linking the library (`util_rules/probes.py`). Each test translation unit
   is scanned and compiled as its own process with the probe's compile line (PUBLIC and
   INTERFACE requirements, transitively, plus the project's flags), linked with its link line
   (the link closure and CMake's driver) against the replayed libraries, and the executable
   runs from the build directory.

## Limitations

- Order-only dependencies are not followed. Generated sources produced by
  `add_custom_command` and discovered only through them are an error at planning time:
  check them in and list them as sources, or generate them with a codegen dependency.
- A scan that fails, or that finds an include the project does not provide, is an error.
  Compiler launchers (`CMAKE_<LANG>_COMPILER_LAUNCHER`, `CMAKE_<LANG>_CLANG_TIDY`) are
  stripped for the scan but kept for the compile, where they need their own environment.
- Ninja `dyndep` (C++ modules), `subninja` and validation inputs are not handled.
- Two dependencies providing one path with different contents fail with the engine's
  digest-merge error, which does not name the targets.
- Debug and coverage builds embed the compile sandbox path (`DW_AT_comp_dir`, `.gcda`
  paths); `-ffile-prefix-map`/`-fprofile-prefix-map` are not injected yet.
- Parametrized projects package to the same `dist/<build_dir>/` path.
- A `cmake_test` needs at least one `cmake_target` dependency that is a static or shared
  library, and all of them must belong to one project. Shared libraries are found at run
  time through an `$ORIGIN`-relative rpath (`@loader_path` on macOS). The probes are added
  in the top-level directory's scope, so directory-scoped settings of a subdirectory do not
  reach a test. The backend owns `CMAKE_PROJECT_INCLUDE`; a project that sets it itself
  loses it.
- `cmake_args` are the same for every platform.
- Pants on Windows is out of scope, and a macOS host is untested.
