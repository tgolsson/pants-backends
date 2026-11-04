"""One cargo invocation as a Pants `Process`.

Every process gets the installed toolchain and the rustup/cargo named caches, a fresh `target/`
inside the sandbox, and, unless disabled, kache as `RUSTC_WRAPPER`: cargo's own incremental
state cannot survive a fresh sandbox, so compiled crates are keyed by content and kept in a
named cache instead. The sandbox root is remapped to a fixed prefix in rustc's output, so
compiled artefacts and kache keys do not depend on the sandbox path."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from pants.core.util_rules.external_tool import download_external_tool
from pants.core.util_rules.system_binaries import (
    BashBinary,
    BinaryPath,
    BinaryPathRequest,
    BinaryShimsRequest,
    SystemBinariesSubsystem,
    create_binary_shims,
    find_binary,
)
from pants.engine.fs import EMPTY_DIGEST, CreateDigest, Digest, FileContent, MergeDigests
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessCacheScope
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants_backend_cargo.subsystems import KacheTool
from pants_backend_cargo.util_rules.platform import TOOLCHAIN_FINGERPRINT_ENV, toolchain_env_names
from pants_backend_cargo.util_rules.rustup import (
    CARGO_NAMED_CACHE,
    RUSTUP_NAMED_CACHE,
    TOOLCHAIN_CACHES,
    RustToolchain,
)

TARGET_DIR = "target"
KACHE_TOOL_DIR = ".kache"
KACHE_NAMED_CACHE = ".kache-data"
SANDBOX_PREFIX = "/pants-sandbox"
SCRIPT_NAME = "__cargo.sh"

# What rustc and build scripts reach through PATH. `cc` is rustc's default linker driver and
# what cc-rs runs; the binutils are what `cc` and build scripts call by name; `getconf` and
# (on Linux) `ldd` are how kache identifies the host libc for its cache key: without them
# every crate is "uncacheable".
_SHIMMED = ("cc", "ld", "ar", "as", "getconf")
_SHIMMED_LINUX = ("ldd",)
# Shimmed when the host has them: what `-sys` crates' build scripts look for.
_OPTIONAL = ("pkg-config",)

# What every cargo process sets itself (see `make_cargo_process`); a package's `env` may not.
_RESERVED_ENV = frozenset(
    {
        "PATH",
        "CARGO",
        "CARGO_HOME",
        "CARGO_TARGET_DIR",
        "CARGO_INCREMENTAL",
        "RUSTUP_HOME",
        "RUSTUP_TOOLCHAIN",
        "RUSTC_WRAPPER",
        "RUSTFLAGS",
        TOOLCHAIN_FINGERPRINT_ENV,
    }
)


def is_reserved_env(key: str, triples: Iterable[str]) -> bool:
    """`triples` are those the process builds for, whose `cc_toolchain` variables it sets."""
    return (
        key in _RESERVED_ENV
        or key.startswith("KACHE_")
        or any(key in toolchain_env_names(t) for t in triples)
    )


def manifest_path(spec_path: str) -> str:
    """`<spec_path>/Cargo.toml`, or `Cargo.toml` for a target at the build root."""
    return os.path.join(spec_path, "Cargo.toml")


@dataclass(frozen=True)
class CargoProcessRequest:
    """`cargo <command>`; or, with `script`, a shell script that runs `$CARGO` itself (the
    test build needs to post-process cargo's output in the same sandbox)."""

    toolchain: RustToolchain
    command: tuple[str, ...]
    description: str
    digest: Digest = EMPTY_DIGEST
    output_files: tuple[str, ...] = ()
    output_directories: tuple[str, ...] = ()
    env: FrozenDict[str, str] = FrozenDict()
    script: str | None = None
    extra_shims: tuple[str, ...] = ()
    tool_paths: tuple[BinaryPath, ...] = ()
    """Shimmed onto PATH under their own names."""
    cache_scope: ProcessCacheScope = ProcessCacheScope.SUCCESSFUL
    level: LogLevel = LogLevel.DEBUG


@rule
async def make_cargo_process(
    request: CargoProcessRequest,
    kache: KacheTool,
    bash: BashBinary,
    platform: Platform,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> Process:
    shims = await create_binary_shims(
        BinaryShimsRequest.for_binaries(
            *_SHIMMED,
            *(_SHIMMED_LINUX if platform.value.startswith("linux") else ()),
            *request.extra_shims,
            rationale="run cargo",
            search_path=system_binaries.system_binary_paths,
        ),
        **implicitly(),
    )
    # The linker comes from the `cc_toolchain` (`CARGO_TARGET_<triple>_LINKER`), not from a
    # `.cargo/config.toml` linker on PATH.
    optional_paths = await concurrently(
        find_binary(
            BinaryPathRequest(binary_name=name, search_path=system_binaries.system_binary_paths),
            **implicitly(),
        )
        for name in _OPTIONAL
    )
    linkers = await create_binary_shims(
        BinaryShimsRequest.for_paths(
            *(paths.first_path for paths in optional_paths if paths.first_path),
            *request.tool_paths,
            rationale="run cargo",
        ),
        **implicitly(),
    )

    env = {
        "PATH": ":".join(
            (
                f"{{chroot}}/{request.toolchain.bin_dir}",
                linkers.path_component,
                shims.path_component,
            )
        ),
        "RUSTUP_HOME": f"{{chroot}}/{RUSTUP_NAMED_CACHE}",
        # cargo finds subcommands (`cargo clippy`) in CARGO_HOME/bin before PATH; those are rustup
        # proxies, which would otherwise run the named cache's default toolchain.
        "RUSTUP_TOOLCHAIN": request.toolchain.name,
        "CARGO_HOME": f"{{chroot}}/{CARGO_NAMED_CACHE}",
        "CARGO_TARGET_DIR": f"{{chroot}}/{TARGET_DIR}",
        # Incremental compilation only pays off when the target directory is reused.
        "CARGO_INCREMENTAL": "0",
        # Overrides `build.rustflags` from a `.cargo/config.toml`; see the README.
        "RUSTFLAGS": f"--remap-path-prefix={{chroot}}={SANDBOX_PREFIX}",
    }
    append_only_caches = dict(TOOLCHAIN_CACHES)
    immutable_input_digests = {
        **shims.immutable_input_digests,
        **linkers.immutable_input_digests,
    }

    if kache.enabled:
        tool = await download_external_tool(kache.get_request(platform))
        immutable_input_digests[KACHE_TOOL_DIR] = tool.digest
        append_only_caches["kache"] = KACHE_NAMED_CACHE
        env.update(
            {
                "RUSTC_WRAPPER": f"{{chroot}}/{KACHE_TOOL_DIR}/kache",
                "KACHE_CACHE_DIR": f"{{chroot}}/{KACHE_NAMED_CACHE}",
                "KACHE_BASE_DIR": "{chroot}",
                "KACHE_MAX_SIZE": kache.cache_size,
                "KACHE_LOCAL_ONLY": "1",
            }
        )

    env.update(request.env)

    cargo = f"{{chroot}}/{request.toolchain.cargo}"
    if request.script is None:
        argv: tuple[str, ...] = (cargo, *request.command)
        input_digest = request.digest
    else:
        env["CARGO"] = cargo
        script = await create_digest(
            CreateDigest([FileContent(SCRIPT_NAME, request.script.encode(), is_executable=True)])
        )
        input_digest = await merge_digests(MergeDigests([request.digest, script]))
        argv = (bash.path, SCRIPT_NAME, *request.command)

    return Process(
        argv=argv,
        input_digest=input_digest,
        description=request.description,
        level=request.level,
        env=env,
        append_only_caches=FrozenDict(append_only_caches),
        immutable_input_digests=FrozenDict(immutable_input_digests),
        output_files=request.output_files,
        output_directories=request.output_directories,
        cache_scope=request.cache_scope,
    )


def rules():
    return collect_rules()
