# pants-backend-cmake

A [Pants](https://www.pantsbuild.org) backend for CMake projects. CMake stays the source of truth:
the backend runs `cmake -G Ninja` once, reads the generated `build.ninja`, and replays every edge
(one translation unit, one archive, one link) as its own cached Pants process. Nothing in
`CMakeLists.txt` is declared twice, and the unit of caching is the edge, not the project.
CMake provides the build artefacts; tests, packaging and dependencies are Pants' business.

**Status: alpha.** Linux, host gcc/clang, Ninja generator, local execution only (the host
compilers are resolved on the machine).

## Targets

- `cmake_project`: the project. `sources` is everything the build reads: CMake files,
  configure-time inputs, sources and headers. Files matching `configure_placeholders`
  (sources and headers by default) are given to configure as zero-byte placeholders, so
  editing one never reruns configure, only adding or removing one does; everything else
  (`CMakeLists.txt`, `*.cmake`, `configure_file` inputs, `file(READ)` inputs) is given as
  is. `dependencies` on `files` targets are mounted next to the sources for the compile and
  link steps (vendored header trees, for example); they are not visible to configure.
  `cmake_args`, `build_dir` (default `build`) and `build_type` are passed to CMake.
- `cmake_target`: one CMake `STATIC`, `SHARED`, `MODULE` library or `EXECUTABLE`, named by
  `project` (the `cmake_project`'s address, inferred as a dependency) and `cmake_name`
  (defaults to the target name). `pants package` writes its outputs at their build paths,
  `dist/<build_dir>/libfoo.a`. Object libraries are internal and are not addressed.
- `cmake_test`: a test executable **Pants** compiles from `sources` (translation units and
  the headers they include) and links against the `cmake_target` libraries in
  `dependencies`, then runs with `args` from the libraries' build directory. The test is not
  a CMake target and nothing is read from CTest, but CMake still decides how a consumer of
  each library is built: configure injects a *consumer probe* per library and language (a
  hidden executable linking the library, see `util_rules/probes.py`), and the test's
  translation units are compiled with the probe's compile line (the library's PUBLIC and
  INTERFACE defines, include directories and options, transitively, plus the project's
  global flags for that language) and linked with the probe's link line (the library,
  everything it links against, system libraries such as `-lm`, CMake's link flags and
  driver). PRIVATE settings of the library do not leak. Libraries are taken in the order
  written; the first one's flags come first. The test adds `include_dirs`, `defines`,
  `compiler_args` and `linker_args`. Translation units found in `files` dependencies are
  compiled in (a vendored test framework); their other files are staged for the run, as are
  `runtime_package_dependencies`. `timeout` and `extra_env_vars` apply. A test that fails to
  compile or link is reported as a failed test.
- `cmake_tests`: a generator: one `cmake_test` per directory with translation units under
  `sources`, named after the directory (`tests:cc#unit/ops/abs`), with that directory's
  translation units and every header matched. The other fields apply to all generated
  tests; `overrides={"c_abi": {"compiler_args": [...]}}` adjusts single ones.

Another backend can run tests under a wrapper (an emulator, a tracer): it subclasses
`CMakeTestWrapperRequest` (`goals/wrapper.py`), implements `is_applicable(target)` and a rule
returning a `CMakeTestWrapper` (an argv prefix, digests mounted read-only into the sandbox,
env), and registers it with a `UnionRule`. Typically it also adds a field to `cmake_test`
with `register_plugin_field`, and to the `cmake_tests` generator with
`register_plugin_field(..., as_moved_field=True)`, and reads it in `is_applicable`. At most
one wrapper may apply to a test, and its env is set over the test's own. Test executables are
built under their address without its parameters, so `parametrize` over such a field (one
that does not change the build) runs one build several ways.

## Subsystems

- `[cmake]`: the pinned CMake download (`version`, `known_versions`) and the host compilers
  (`c_compiler`, `cxx_compiler`, resolved on the system binary path). The `--version` output
  of the compilers and of `ar`, `ranlib` and `ld` is fingerprinted into every process, so a
  host toolchain upgrade on a persistent cache invalidates correctly. (glibc and kernel
  headers are not fingerprinted; a downloaded toolchain would put the whole toolchain in the
  key by digest.)
- `[ninja]`: the pinned Ninja download. Ninja is only needed by configure; the replay never
  runs it.
- `[cmake-test-runner]`: `skip`.

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
4. **Test** (`pants test`): each test translation unit is scanned and compiled as its own
   process with the consumer probe's flags, linked with the probe's link line against the
   libraries built by the replay, and the executable runs from the build directory on the
   libraries' runtime outputs.

## Limitations

- Order-only dependencies are not followed. Generated sources produced by
  `add_custom_command` and discovered only through them are an error at planning time:
  check them in, or list them as sources.
- A scan that fails, or that finds an include the project does not provide, is an error.
  Compiler launchers (`CMAKE_<LANG>_COMPILER_LAUNCHER`, `CMAKE_<LANG>_CLANG_TIDY`) are
  stripped for the scan but kept for the compile, where they need their own environment.
- Ninja `dyndep` (C++ modules), `subninja` and validation inputs are not handled.
- Debug and coverage builds embed the compile sandbox path (`DW_AT_comp_dir`, `.gcda`
  paths); `-ffile-prefix-map`/`-fprofile-prefix-map` are not injected yet.
- Parametrized projects package to the same `dist/<build_dir>/` path.
- A `cmake_test` needs at least one `cmake_target` dependency that is a static or shared
  library, and all of them must belong to one project. Shared libraries are found at run
  time through an `$ORIGIN`-relative rpath (`@loader_path` on macOS). The probes are added
  in the top-level directory's scope, so directory-scoped settings of a subdirectory do not
  reach a test. The backend owns `CMAKE_PROJECT_INCLUDE`; a project that sets it itself
  loses it.
- Windows is out of scope; macOS needs the `[cmake]` download pins and a compiler mapping.
