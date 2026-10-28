# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Running the resolver in a sandboxed process.

`apt-resolver` is a scie built here from the stdlib-only resolver modules: one executable with its
own pinned Python, so a resolve names nothing on the host and its cache key is the same on every
machine.

The scie unpacks its Python and installs its venv under `~/.cache` on first run. A resolve points
`HOME` at a named cache, so that happens once per machine rather than once per sandbox. The scie
keys its venv setup on the path it is given, so that path must also be the same in every sandbox:
`/proc/self/cwd` gives one on Linux.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

from pants.backend.python.util_rules import pex_cli
from pants.backend.python.util_rules.pex_cli import PexCliProcess
from pants.core.util_rules.system_binaries import SystemBinariesSubsystem
from pants.engine.fs import EMPTY_DIGEST, AddPrefix, CreateDigest, Digest, FileContent, RemovePrefix
from pants.engine.intrinsics import add_prefix, create_digest, remove_prefix
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_distroless import apt_resolver, openpgp
from pants_backend_distroless.subsystem import AptSubsystem

_MODULES = (apt_resolver, openpgp)
_SOURCES = "__apt_resolver_src"
_OUTPUT = "__apt_resolver"
_EXE = f"{_OUTPUT}/apt-resolver"
_CACHE = "apt_resolver"


def resolver_sources() -> CreateDigest:
    """The resolver modules at their package path, with an empty `__init__.py` so importing them
    doesn't import the plugin and Pants."""
    # Read through `importlib.resources` so this also works when the plugin is imported from a zip.
    package = resources.files(__package__)
    files = [FileContent(f"{__package__}/__init__.py", b"")]
    for module in _MODULES:
        name = module.__name__.rpartition(".")[2]
        files.append(FileContent(f"{__package__}/{name}.py", package.joinpath(f"{name}.py").read_bytes()))
    return CreateDigest(files)


@dataclass(frozen=True)
class AptResolver:
    """The `apt-resolver` executable: `digest` holds it, mounted so that it is at `exe`. `home` is
    the sandbox's named cache, where it unpacks."""

    digest: Digest
    exe: str = _EXE
    home: str = f"{{chroot}}/.{_CACHE}"

    def process(
        self, *args: str, description: str, input_digest: Digest = EMPTY_DIGEST, **kwargs: Any
    ) -> Process:
        """A process resolving with `args` in a sandbox holding `input_digest`."""
        return Process(
            argv=(self.exe, *args),
            description=description,
            input_digest=input_digest,
            immutable_input_digests={_OUTPUT: self.digest},
            # The scie and pex record the cache's real path, so what they install outlives the sandbox.
            env={"HOME": self.home},
            append_only_caches={_CACHE: f".{_CACHE}"},
            **kwargs,
        )


@rule(desc="Build apt-resolver", level=LogLevel.DEBUG)
async def build_apt_resolver(
    apt: AptSubsystem, system_binaries: SystemBinariesSubsystem.EnvironmentAware, platform: Platform
) -> AptResolver:
    sources = await create_digest(resolver_sources())
    sources = await add_prefix(AddPrefix(sources, _SOURCES))
    # Only this process names the host (Pants' Python runs pex); its output is the same everywhere.
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            PexCliProcess(
                subcommand=(),
                extra_args=(
                    "--output-file",
                    _EXE,
                    f"--sources-directory={_SOURCES}",
                    f"--entry-point={apt_resolver.__name__}:main",
                    # Pex otherwise names the building Python's minor version.
                    "--python-shebang=#!/usr/bin/env python3",
                    # Runs from an installed venv, so a warm start costs no more than plain Python
                    # (once its setup is shared; see `home`).
                    "--venv",
                    "--scie=eager",
                    "--scie-only",
                    # Its own Python, independent of the one running Pants.
                    f"--scie-pbs-release={apt.resolver_pbs_release}",
                    f"--scie-python-version={apt.resolver_python_version}",
                ),
                additional_input_digest=sources,
                # Building the scie runs `ldd` to find the host's libc.
                extra_env={"PATH": ":".join(system_binaries.system_binary_paths)},
                output_directories=(_OUTPUT,),
                description="Build apt-resolver",
                level=LogLevel.DEBUG,
            )
        )
    )
    digest = await remove_prefix(RemovePrefix(result.output_digest, _OUTPUT))
    if platform in (Platform.linux_x86_64, Platform.linux_arm64):
        return AptResolver(digest, home=f"/proc/self/cwd/.{_CACHE}")
    # Elsewhere `{chroot}` keeps the sandbox's path out of the cache key, but each sandbox redoes the
    # scie's venv setup (a fraction of a second).
    return AptResolver(digest)


def rules():
    return [*collect_rules(), *pex_cli.rules()]
