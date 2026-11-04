# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

Moved into [pants-backends](https://github.com/tgolsson/pants-backends) and renamed from
`pants_cargo_porcelain` to `pants-backend-cargo`: the backends are now `pants_backend_cargo` and
`pants_backend_cargo.backends.clippy`. It is MIT-licensed like the other plugins there, instead of
Apache-2.0.

A port to Pants 2.32 on CPython 3.14, with the rules written in the call-by-name API. Pants 2.24
and Python 3.9 are no longer supported.

### Toolchain and caching

The toolchain can now come from the `rust-toolchain.toml` channel when `[rustup].rust_version` is
unset, and is always installed with clippy and rustfmt. Every cargo process gets a fresh
`target/`, and the new `[kache]` caches compiled crates across sandboxes in place of sccache.

### Platforms

A `cargo_package`'s new `platforms` field builds its binaries for the enabled `platform` targets
of pants-backend-platforms, under `dist/<platform>/`. The platform's `cc_toolchain`, from
pants-backend-cc, links and serves cc-rs. Both backends are dependencies, pinned to a
pants-backends commit until they are released.

### Other changes

- Workspace members honour the manifest's `exclude`.
- `dependencies` can also stage `resources` and packageable targets of other backends.
- The new `env` field sets variables for every cargo process whose sandbox holds the package.
- `test` builds once per workspace with `cargo test --no-run --workspace`, then runs each
  executable as its own process.
- `fmt` and `lint` (clippy) run once per workspace instead of once per package.
- `pants package pants-plugins/cargo:package` builds the distribution.

### Removed

- `[sccache]` and `[binstall]`, along with the cargo-mtime and cargo-binstall tools.
- The `toml` dependency.
