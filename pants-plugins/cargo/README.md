# pants-backend-cargo

A [Pants](https://www.pantsbuild.org) backend for Rust that wraps Cargo rather than replacing
it: Cargo keeps its package model, its lockfiles and its workspaces, and Pants adds target
addresses, caching, a hermetic toolchain and the usual goals on top.

This release targets Pants **2.32.x**. Install it with `pants-backend-cargo==0.1.0` in
`[GLOBAL].plugins` in `pants.toml`, and enable `pants_backend_cargo` in
`[GLOBAL].backend_packages`.

**Status: alpha.** No stability guarantees until it nears feature completeness.

## Targets

- `cargo_workspace`: the root of a Cargo workspace: `Cargo.toml`, `Cargo.lock`,
  `rust-toolchain.toml` and `.cargo/config.toml`. Members are found through the manifest's
  `members` (globs one path segment at a time, less `exclude`) and depend on it. Test
  executables and clippy run once per workspace. Members cannot inherit from the workspace
  (`version.workspace = true`, `b = { workspace = true }`): a package's targets are generated
  from its own manifest alone.
- `cargo_package`: a package. `cargo metadata` on its manifest generates
  - `path:name#package` (`cargo_package_impl`): the sources and cargo dependencies, what
    `fmt` and `lint` act on;
  - `path:name#library` (`cargo_library`), one `cargo_binary` per `[[bin]]` named after it,
    one `cargo_test` per `[[test]]`: what `test` runs, and for binaries what `package` and
    `run` build.

  `dependencies` adds `files`/`resources` targets the build reads (`include_str!`, build
  script inputs) and packageable targets of other backends whose `package` output the build
  needs: they are built and staged in the sandbox at their package path for `package`,
  `run`, `test` and `lint` (not for `fmt` and `generate-lockfiles`). Their own dependencies
  are their backend's inputs and are not staged. `env` sets `KEY=VALUE` entries for every
  cargo process whose sandbox holds the package (its own, those of packages depending on it,
  and the per-workspace `test` build, `fmt` and `lint`), and for the test executables and
  `run` binaries those build; `{chroot}` is the sandbox root, so
  `env=["MYLIB_DIR={chroot}/build"]` points a build script at a staged library. `PATH`,
  `CARGO`, `CARGO_HOME`, `CARGO_TARGET_DIR`, `CARGO_INCREMENTAL`, `RUSTUP_HOME`, `RUSTUP_TOOLCHAIN`,
  `RUSTC_WRAPPER`, `RUSTFLAGS`, `KACHE_*` and the toolchain's `CARGO_TARGET_<TRIPLE>_LINKER`,
  `CC_<triple>`, `CXX_<triple>` and `AR_<triple>` are the backend's and rejected. `skip_tests` as
  usual; `output_path` is the directory under `dist/` (default `path.to.target/`), the file
  keeps the binary's name. A target with `test = false` in
  `Cargo.toml` gets `skip_tests`. A `[[bin]]` or `[[test]]` named `package` or `library`, or a
  bin and a test of the same name, is an error: rename it.

`path` dependencies between packages are inferred. `pants tailor` adds both target types.
Pants reads nothing `.gitignore` ignores, so a workspace's `.cargo/config.toml` must not be
(`!/.cargo/` after a `.cargo` rule).

## Toolchain

Nothing from the host's `~/.rustup` or `~/.cargo` is used. `[rustup]` pins rustup-init, which
installs the toolchain into a named cache shared by every sandbox. The version is
`[rustup].rust_version` or, unset, the `channel` of the `rust-toolchain.toml` at the build
root, and must be exact: it is part of every cache key. The components the file lists are
installed with it; `fmt` and `lint` add `rustfmt` and `clippy`.

Every cargo process runs in a fresh sandbox with its own `target/`, so cargo's own incremental
state never carries over. Instead, `[kache]` puts [kache](https://github.com/kunobi-ninja/kache)
in front of rustc: compiled crates are keyed by their inputs and kept in a named cache, and
unchanged crates are restored rather than rebuilt, across sandboxes and across packages of a
workspace. Local only, no daemon. `--remap-path-prefix` maps the sandbox root to a fixed
prefix so rustc's output does not depend on the sandbox path; this sets `RUSTFLAGS`, so a
`build.rustflags` in `.cargo/config.toml` is not read. Its `[target.<triple>] linker` is only
for plain `cargo`: under Pants the platform's `cc_toolchain` links (see Platforms).

## Platforms

The backend builds for the `platform` targets of
[pants-backend-platforms](https://github.com/tgolsson/pants-backends). A `cargo_package`'s
`platforms` field lists what its binaries can be built for, the host alone when unset; `pants
package` builds those that are the host or in `[platforms].enabled`, the host's binary at its
usual path and every other under `dist/<platform>/`, with `.exe` for Windows.

```python
platform(name="windows-x86_64", os="windows", arch="x86_64")  # builds as x86_64-pc-windows-gnu
cargo_package(platforms=["linux-x86_64", "windows-x86_64"])
```

- A `platform` builds as its `rust_target`, by default the triple of its `os` and `arch`
  (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `x86_64-pc-windows-gnu`,
  `aarch64-apple-darwin`, `x86_64-apple-darwin`). Every enabled platform's target is installed
  with the toolchain. Another platform's build passes `--target`; the host's does not.
- The platform's `cc_toolchain` ([pants-backend-cc](https://github.com/tgolsson/pants-backends))
  links the binary and is cc-rs's compiler for that triple, host included; a cross build also
  gets the host's for its build scripts and proc macros.
- A packageable dependency of another backend that builds for platforms (a `cmake_target`)
  is built for the same platform, and must list it in its own `platforms`. Cargo packages
  that a binary depends on need no `platforms` of their own.
- `run`, `test`, `fmt`, `lint` and `generate-lockfiles` build for the host only; `test` and
  `lint` do not pass `--target`, even for a host whose `rust_target` is not the machine's.

## Goals

- `package`: `cargo build --locked --bin <name>` (`--release` with `[rust].release`), the
  binary at `dist/<path.to.target>/<name>` or under `output_path`.
- `run`: the packaged binary, with the `files`, `resources` and staged packages of its closure,
  but not its cargo sources.
- `test`: one `cargo test --no-run --workspace` per workspace (or per package outside any),
  then one process per test executable, so each `cargo_library`, `cargo_binary` and
  `cargo_test` is its own cached test result. Doctests are not run. The executables run from
  the package directory with the sources at hand and `CARGO_MANIFEST_DIR` set to it, as
  `cargo test` does, but a path compiled in through `env!("CARGO_MANIFEST_DIR")` or
  `env!("CARGO_BIN_EXE_<name>")` names the build sandbox, which is gone when the test runs:
  read `CARGO_MANIFEST_DIR` with `std::env::var` instead. A target cargo builds no executable
  for (its `required-features` are off) is skipped with a warning.
- `fmt` / `lint`: `cargo fmt` (`[rust].skip`) and, with
  `pants_backend_cargo.backends.clippy`, `cargo clippy` (`[clippy].args`, `[clippy].skip`),
  once per workspace.
- `generate-lockfiles`: `cargo update`, never cached across runs; the resolve names are the workspace and package
  addresses (`pants generate-lockfiles --resolve=path:name#package`), and a member's name
  updates its workspace's `Cargo.lock`.

## Setup

Install `pants-backend-cargo` in `[GLOBAL].plugins` in `pants.toml`, and enable it:

```toml
[GLOBAL]
backend_packages = [
  "pants_backend_cargo",
  "pants_backend_cargo.backends.clippy",
]

[rustup]
rust_version = "1.97.0"  # or a rust-toolchain.toml at the build root

[clippy]
args = ["--", "-Dwarnings"]

[kache]
enabled = true  # the default; false runs rustc bare
```

Linux and macOS. With no `cc_toolchain` targets, the host's `gcc`, `g++` and `ar` link.
Declaring `platform` and `cc_toolchain` targets needs `pants_backend_platforms` and
`pants_backend_cc` in `backend_packages` as well; `pants-backend-cargo` installs both.

## License

MIT ([LICENSE-MIT.txt](LICENSE-MIT.txt)).
