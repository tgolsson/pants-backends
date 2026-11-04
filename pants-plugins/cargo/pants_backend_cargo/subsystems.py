from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption, SkipOption, StrOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap

# The Rust target a `platform` builds as when it sets no `rust_target`, by (os, arch).
DEFAULT_TRIPLES = {
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
    ("windows", "x86_64"): "x86_64-pc-windows-gnu",
    ("macos", "aarch64"): "aarch64-apple-darwin",
    ("macos", "x86_64"): "x86_64-apple-darwin",
}

# The machines Pants runs on, by `Platform` value.
PLATFORM_TRIPLES = {
    "linux_arm64": DEFAULT_TRIPLES["linux", "aarch64"],
    "linux_x86_64": DEFAULT_TRIPLES["linux", "x86_64"],
    "macos_arm64": DEFAULT_TRIPLES["macos", "aarch64"],
    "macos_x86_64": DEFAULT_TRIPLES["macos", "x86_64"],
}


class RustSubsystem(Subsystem):
    options_scope = "rust"
    name = "rust"
    help = "General settings for Rust."

    tailor = BoolOption(
        default=True,
        help="If true, add `cargo_package` and `cargo_workspace` targets with the `tailor` goal.",
        advanced=True,
    )
    release = BoolOption(
        default=False,
        help="If true, `package` and `run` build with `--release`.",
        advanced=True,
    )
    # Only rustfmt: clippy has its own `[clippy].skip`.
    skip = SkipOption("fmt")


class CargoTestSubsystem(Subsystem):
    # Not `cargo-test`: option scopes normalise to `cargo_test`, the target alias.
    options_scope = "cargo-test-runner"
    name = "cargo test"
    help = "Runs the test executables `cargo test --no-run` builds, one process per executable."

    skip = SkipOption("test")


class RustupTool(ExternalTool):
    """rustup-init, pinned. It installs the Rust toolchain into a named cache."""

    options_scope = "rustup"
    name = "rustup"
    help = softwrap("""
        rustup installs the Rust toolchain into a named cache shared by every sandbox, so no
        host rustup or cargo is used. The version comes from `rust_version` or, unset, from
        the `channel` of the `rust-toolchain.toml` at the build root.
        """)

    default_version = "1.28.2"
    default_known_versions = [
        "1.28.2|linux_arm64|e3853c5a252fca15252d07cb23a1bdd9377a8c6f3efa01531109281ae47f841c|19321944",
        "1.28.2|linux_x86_64|20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c|20889000",
        "1.28.2|macos_arm64|20ef5516c31b1ac2290084199ba77dbbcaa1406c45c1d978ca68558ef5964ef5|11174016",
        "1.28.2|macos_x86_64|9c331076f62b4d0edeae63d9d1c9442d5fe39b37b05025ec8d41c5ed35486496|12887404",
    ]

    rust_version = StrOption(
        default=None,
        help=softwrap("""
            The Rust toolchain to install, an exact version such as `1.97.0`. Unset, the
            `channel` of the `rust-toolchain.toml` at the build root is used, which keeps
            Pants and a developer's own `cargo` on the same toolchain. A moving channel
            (`stable`, `nightly`) is rejected: the toolchain is part of every cache key.
            """),
    )

    def generate_url(self, plat: Platform) -> str:
        triple = PLATFORM_TRIPLES[plat.value]
        return f"https://static.rust-lang.org/rustup/archive/{self.version}/{triple}/rustup-init"

    def generate_exe(self, plat: Platform) -> str:
        return "./rustup-init"


class KacheTool(ExternalTool):
    """kache, a content-keyed compiler cache, as `RUSTC_WRAPPER`."""

    options_scope = "kache"
    name = "kache"
    help = softwrap("""
        kache (github.com/kunobi-ninja/kache) in front of rustc. Every cargo process runs in
        a fresh sandbox with its own target directory, so cargo's mtime-based incremental
        state never carries over; kache keys compiled crates by their inputs and keeps them
        in a named cache, so the unchanged crates of a workspace are restored instead of
        rebuilt. Local only: no daemon, no remote.
        """)

    default_version = "1.2.2"
    default_known_versions = [
        "1.2.2|linux_arm64|38856cee613f6ee0ec579099fc9205b840a0b45bb763f4b8fa782d25d93c18ef|11697986",
        "1.2.2|linux_x86_64|f2f6555dee50945c2a90a7fb793c2084a00cc6459d08ac2306d38d905064321b|12761895",
        "1.2.2|macos_arm64|10d09e146852a8df8796cf642ca58872107a7f391b7647ec8cc253cf289af0a9|10600400",
        "1.2.2|macos_x86_64|9a1c3ecc89cf36b23512a93868226140d46f1f70cf6cb648bfe08eeafb25a368|12389217",
    ]

    enabled = BoolOption(
        default=True,
        help=("If false, rustc runs without the cache and every cargo process compiles from scratch."),
    )
    cache_size = StrOption(
        default="4GiB",
        help="`KACHE_MAX_SIZE`: the on-disk budget of the compiler cache; kache evicts beyond it.",
        advanced=True,
    )

    def generate_url(self, plat: Platform) -> str:
        triple = {
            "linux_arm64": "aarch64-unknown-linux-musl",
            "linux_x86_64": "x86_64-unknown-linux-musl",
            "macos_arm64": "aarch64-apple-darwin",
            "macos_x86_64": "x86_64-apple-darwin",
        }[plat.value]
        return (
            f"https://github.com/kunobi-ninja/kache/releases/download/v{self.version}/kache-{triple}.tar.gz"
        )

    def generate_exe(self, plat: Platform) -> str:
        return "./kache"


def rules():
    return [
        *RustSubsystem.rules(),
        *CargoTestSubsystem.rules(),
        *RustupTool.rules(),
        *KacheTool.rules(),
    ]
