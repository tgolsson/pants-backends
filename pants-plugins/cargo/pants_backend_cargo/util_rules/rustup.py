"""The Rust toolchain. rustup-init (a pinned download) installs an exact version into named
caches for `RUSTUP_HOME` and `CARGO_HOME`, shared by every sandbox and every session; nothing
from the host's `~/.rustup` or `~/.cargo` is used. The version comes from `[rustup].rust_version`
or the `rust-toolchain.toml` at the build root."""

from __future__ import annotations

import dataclasses
import logging
import re
import tomllib
from dataclasses import dataclass

from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.core.util_rules.external_tool import download_external_tool
from pants.engine.fs import CreateDigest, FileContent, MergeDigests, PathGlobs
from pants.engine.intrinsics import (
    create_digest,
    get_digest_contents,
    merge_digests,
    path_globs_to_digest,
)
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessCacheScope, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import AllTargets
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants_backend_cargo.subsystems import RustupTool
from pants_backend_cargo.util_rules.platform import platform_triple, rust_triple

from pants_backend_platforms.subsystems import PlatformsSubsystem
from pants_backend_platforms.util_rules.platforms import KnownPlatforms

logger = logging.getLogger(__name__)

RUSTUP_NAMED_CACHE = ".rustup-home"
CARGO_NAMED_CACHE = ".cargo-home"
TOOLCHAIN_CACHES = FrozenDict({"rustup": RUSTUP_NAMED_CACHE, "cargo": CARGO_NAMED_CACHE})
TOOLCHAIN_FILE = "rust-toolchain.toml"

_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$|^nightly-\d{4}-\d{2}-\d{2}$")

_TOOLCHAIN_ENV = FrozenDict(
    {
        "RUSTUP_HOME": f"{{chroot}}/{RUSTUP_NAMED_CACHE}",
        "CARGO_HOME": f"{{chroot}}/{CARGO_NAMED_CACHE}",
    }
)


@dataclass(frozen=True)
class RustVersion:
    """The toolchain to install: an exact version and the components `rust-toolchain.toml`
    asks for, plus clippy and rustfmt for `lint` and `fmt`."""

    version: str
    components: tuple[str, ...]


@rule(desc="Determine the Rust version", level=LogLevel.DEBUG)
async def determine_rust_version(rustup: RustupTool) -> RustVersion:
    contents = await get_digest_contents(await path_globs_to_digest(PathGlobs([TOOLCHAIN_FILE])))
    channel: str | None = None
    components: tuple[str, ...] = ()
    if contents:
        toolchain = tomllib.loads(contents[0].content.decode()).get("toolchain", {})
        channel = toolchain.get("channel")
        components = tuple(toolchain.get("components", ()))

    # One toolchain request per session, whatever the goal (see `rust_toolchain_request`): a
    # second `rustup` run while a cargo process executes a proxy in CARGO_HOME/bin fails with
    # "text file busy".
    for component in ("clippy", "rustfmt"):
        if component not in components:
            components = (*components, component)

    version = rustup.rust_version or channel
    if not version:
        raise ValueError(
            f"No Rust version: set `[rustup].rust_version`, or add a `{TOOLCHAIN_FILE}` with "
            '`[toolchain] channel = "<version>"` at the build root.'
        )
    if not _EXACT_VERSION.match(version):
        raise ValueError(
            f"The Rust version must be exact (`1.97.0`, `nightly-2026-01-31`), not `{version}`: "
            "the toolchain is part of every cache key."
        )
    return RustVersion(version, components)


@dataclass(frozen=True)
class RustToolchainRequest:
    version: str
    targets: tuple[str, ...]
    components: tuple[str, ...] = ()


def machine_toolchain_request(version: RustVersion, platform: Platform) -> RustToolchainRequest:
    """Target generation's: it cannot wait for the `platform` targets, which wait for it."""
    return RustToolchainRequest(version.version, (platform_triple(platform),), version.components)


@rule(desc="Choose the Rust targets to install", level=LogLevel.DEBUG)
async def rust_toolchain_request(
    version: RustVersion,
    known: KnownPlatforms,
    platforms: PlatformsSubsystem,
    platform: Platform,
    # Waiting for every target orders this install after each generator's own install and
    # `cargo metadata`, so the second `rustup` run of a session overlaps no cargo process.
    all_targets: AllTargets,
) -> RustToolchainRequest:
    """Every enabled platform's target, built or not, so one install serves the session."""
    machine = machine_toolchain_request(version, platform)
    triples = []
    for p in (known.host, *(known.get(n, "[platforms].enabled") for n in platforms.enabled)):
        triple = rust_triple(p, all_targets)
        if triple is None:
            # Only a build for it needs one, and `rust_target` says so.
            logger.debug(f"Not installing a Rust target for `{p.name}`: it has no triple.")
        else:
            triples.append(triple)
    return dataclasses.replace(machine, targets=tuple(dict.fromkeys((*machine.targets, *triples))))


@dataclass(frozen=True)
class RustToolchain:
    """An installed toolchain, as a sandbox-relative path into the rustup named cache."""

    path: str
    version: str
    targets: tuple[str, ...]

    @property
    def name(self) -> str:
        """The rustup toolchain name, as `RUSTUP_TOOLCHAIN` takes it."""
        return self.path.rsplit("/", 1)[-1]

    @property
    def bin_dir(self) -> str:
        return f"{self.path}/bin"

    @property
    def cargo(self) -> str:
        return f"{self.bin_dir}/cargo"


# Runs its commands, separated by `--`, holding an exclusive lock on its first argument: two
# Pants sessions on one machine share the named cache. Waiting happens here, in the process,
# never on an engine thread.
_LOCKED_INSTALL = """\
import fcntl, subprocess, sys

with open(sys.argv[1], "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    commands = [[]]
    for arg in sys.argv[2:]:
        if arg == "--":
            commands.append([])
        else:
            commands[-1].append(arg)
    for command in commands:
        code = subprocess.call(command)
        if code:
            sys.exit(code)
"""


@rule(desc="Install the Rust toolchain", level=LogLevel.DEBUG)
async def install_rust_toolchain(
    request: RustToolchainRequest,
    rustup: RustupTool,
    platform: Platform,
    python: PythonBuildStandaloneBinary,
) -> RustToolchain:
    rustup_init = await download_external_tool(rustup.get_request(platform))
    components = [f"--component={c}" for c in request.components]

    script = await create_digest(CreateDigest([FileContent("locked_install.py", _LOCKED_INSTALL.encode())]))
    # rustup is idempotent and quick when everything is installed, so this runs once per session
    # rather than being memoised forever: a process-cache hit must not be able to claim a
    # toolchain that a wiped named cache no longer holds.
    await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=[
                    python.path,
                    "locked_install.py",
                    f"{CARGO_NAMED_CACHE}/.pants-install.lock",
                    rustup_init.exe,
                    "-y",
                    "--no-modify-path",
                    "--no-update-default-toolchain",
                    "--default-toolchain=none",
                    "--profile=minimal",
                    "--",
                    f"{{chroot}}/{CARGO_NAMED_CACHE}/bin/rustup",
                    "toolchain",
                    "install",
                    request.version,
                    "--no-self-update",
                    "--profile=minimal",
                    *(f"--target={t}" for t in request.targets),
                    *components,
                ],
                input_digest=await merge_digests(MergeDigests([rustup_init.digest, script])),
                description=f"Install Rust {request.version} for {', '.join(request.targets)}",
                level=LogLevel.DEBUG,
                append_only_caches={**TOOLCHAIN_CACHES, **python.APPEND_ONLY_CACHES},
                env=_TOOLCHAIN_ENV,
                cache_scope=ProcessCacheScope.PER_SESSION,
            )
        )
    )

    host = platform_triple(platform)
    return RustToolchain(
        path=f"{RUSTUP_NAMED_CACHE}/toolchains/{request.version}-{host}",
        version=request.version,
        targets=request.targets,
    )


def rules():
    return collect_rules()
