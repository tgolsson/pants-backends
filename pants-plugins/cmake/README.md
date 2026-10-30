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
  provide is an error (drop it with `"!gen/registry.cpp"`). `cmake_args` and `build_dir`
  (default `build`) are passed to CMake.

  The project is a target generator: it generates a `cmake_target` `<project>#<name>` for every
  library and executable CMake defines. They are found by a configure that sees `sources` as
  above (files matching `configure_placeholders` as placeholders, the rest as they are) and,
  as placeholders, the paths its explicit `dependencies` provide that match
  `configure_placeholders`, so no generator runs. Only explicit dependencies count, less those
  a `!` entry names, and only `files`, `cc_sources` and targets whose backend declares their
  outputs ahead of time (a `DeclaredOutputsFieldSet` member, such as `cpp_generate`) provide
  paths. It configures once for each platform the project targets (its `platforms` and those
  in `overrides`, the host when unset) that this host has a toolchain for.
  `platforms` and `build_type` on the project apply to the generated targets, and
  `overrides={"app": {...}}` adjusts one of them (but not its `dependencies`, `project` or
  `cmake_name`). Each depends on `<project>#_inputs` (a `cmake_project_inputs`), which owns the
  project's files and dependencies, so `--changed-dependents` reaches it. Goals that replace a
  generator with what it generates (`lint ::`) run the project's own tools, such as
  clang-tidy, on `#_inputs`, with its `build_type`.

  A setup mistake (a path both `sources` and a dependency provide, a dependency address that
  does not resolve, an unknown platform name, a `build_dir` outside the project) or a failed
  configure does not stop the rest of the repository: the project, or just that platform for a
  failed configure, generates no targets and the error is logged. A platform without a working
  toolchain is a warning, and one no `cc_toolchain` builds for is logged only at debug level.
  It is logged whenever Pants generates the project's targets: on every run without pantsd, and
  with pantsd again after the generation's inputs change (a BUILD file or CMake file edit) or
  pantsd restarts. `pants package` of a hand-written `cmake_target` naming the project raises
  the error, as does `pants lint <project>#_inputs` with clang-tidy enabled, for the host only.
- `cmake_target`: one CMake `STATIC`, `SHARED`, `MODULE` library or `EXECUTABLE`, named by
  `project` (the `cmake_project`'s address, inferred as a dependency unless the project
  generated it) and `cmake_name` (defaults to the target name); `build_type` sets its
  configure's `CMAKE_BUILD_TYPE`, and when unset the project's `build_type` applies (for a
  parametrized project, the one `project` names, as in `:proj@build_type=Release`). Generated
  ones are the usual way; a hand-written one works the same. `pants package` writes its outputs
  at their build paths, `dist/<build_dir>/libfoo.a`, and those for other `platforms` under
  `dist/<platform>/` (see Platforms). Object libraries are internal and are not addressed.

## C/C++ tools

A static or shared `cmake_target` is a [pants-backend-cc](../cc) library, so a `cc_test` or
`cc_binary` depending on it compiles and links in the project's build directory as CMake would
build a consumer of it (see How it works); nothing PRIVATE leaks. `clang_tidy_paths` on
`cmake_project` selects what `pants_backend_cc.lint.clang_tidy` lints, and `compile_cost_paths`
and `compile_cost_exempt_paths` what `pants_backend_cc.lint.compile_cost` measures and gates.
A `cpp_generate` target (`pants_backend_cc.generate`) can be a dependency of a `cmake_project`
or `cc_test`.

## Platforms

The compilers and `ar` come from the `cc_toolchain` ([pants-backend-cc](../cc)) for the host
and the platform being built; without any, `gcc`, `g++` and `ar` for the host. A
`cmake_target` builds for its `platforms`, and for the host when a `cc_test` or `cc_binary`
links it. Enable `pants_backend_platforms` and `pants_backend_cc` to declare `platform` and
`cc_toolchain` targets.

The backend adds `cmake_toolchain_file` to `cc_toolchain`, passed as `CMAKE_TOOLCHAIN_FILE`: a
cross build needs one for `CMAKE_SYSTEM_NAME`. See the field's help for what it may contain.

## Subsystems

- `[cmake]`: the pinned CMake download (`version`, `known_versions`).
- `[ninja]`: the pinned Ninja download. Ninja is only needed by configure; the replay never
  runs it.

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
4. **Link interface** (`cc_test`): configure injects a *consumer probe* per library and
   language, a hidden executable linking the library (`util_rules/probes.py`). A consumer
   compiles with the probe's compile line (PUBLIC and INTERFACE requirements, transitively,
   plus the project's flags) and links with its link line (the link closure and CMake's
   driver) against the replayed libraries, in the build directory.

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
- The `cmake_target`s one `cc_test` or `cc_binary` links must belong to one project. The
  probes are added in the top-level directory's scope, so directory-scoped settings of a
  subdirectory do not reach a consumer. The backend owns `CMAKE_PROJECT_INCLUDE`; a project
  that sets it itself loses it.
- `cmake_args` are the same for every platform.
- Pants on Windows is out of scope, and a macOS host is untested.
