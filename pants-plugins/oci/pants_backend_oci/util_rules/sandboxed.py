# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Running the plugin's pure builder modules in a sandboxed process.

Rules only decide what to build; the work itself happens in a process, by a stdlib-only module run
through `oci-builder`. That is a scie built here from the modules' source: one executable with its
own pinned Python, so a builder process names nothing on the host and its cache key is the same on
every machine.

The scie unpacks its Python and installs its venv under `~/.cache` on first run. A builder process
points `HOME` at a named cache, so that happens once per machine rather than once per sandbox, and
needs no home directory of its own. The scie keys its venv setup on the path it is given, so that
path must also be the same in every sandbox: `/proc/self/cwd` gives one on Linux.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from types import ModuleType
from typing import Any, Mapping

from pants.backend.python.util_rules import pex_cli
from pants.backend.python.util_rules.pex_cli import PexCliProcess
from pants.core.util_rules.system_binaries import SystemBinariesSubsystem
from pants.engine.fs import EMPTY_DIGEST, AddPrefix, CreateDigest, Digest, FileContent, RemovePrefix
from pants.engine.intrinsics import add_prefix, create_digest, remove_prefix
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciSubsystem
from pants_backend_oci.util_rules import (
    builder_main,
    deb_layer_builder,
    helper_layer_builder,
    layer_index,
    oci_layout_builder,
    podman_builder,
    podman_runner,
)

_MODULES = (
    builder_main,
    deb_layer_builder,
    helper_layer_builder,
    layer_index,
    oci_layout_builder,
    podman_builder,
    podman_runner,
)
_SOURCES = "__oci_builder_src"
_OUTPUT = "__oci_builder"
_EXE = f"{_OUTPUT}/oci-builder"
_CACHE = "oci_builder"


def sandboxed_modules(*modules: ModuleType) -> CreateDigest:
    """The modules at their package paths, with empty `__init__.py`s for the packages, so their
    imports of each other still resolve."""
    files = []
    packages = set()
    for module in modules:
        package, _, name = module.__name__.rpartition(".")
        # Read through `importlib.resources` so this also works when the plugin is imported from a zip.
        source = resources.files(package).joinpath(f"{name}.py").read_bytes()
        files.append(FileContent(f"{module.__name__.replace('.', '/')}.py", source))
        parts = package.split(".")
        packages.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    files.extend(FileContent(f"{package}/__init__.py", b"") for package in sorted(packages))
    return CreateDigest(files)


@dataclass(frozen=True)
class OciBuilder:
    """The `oci-builder` executable: `digest` holds it, mounted so that it is at `exe`. `home` is
    the sandbox's named cache, where it unpacks."""

    digest: Digest
    exe: str = _EXE
    home: str = f"{{chroot}}/.{_CACHE}"

    @property
    def immutable_input_digests(self) -> dict[str, Digest]:
        return {_OUTPUT: self.digest}

    def argv(self, module: ModuleType, *args: str) -> tuple[str, ...]:
        """Run `module.main` with `args`."""
        name = module.__name__.rpartition(".")[2]
        assert name in builder_main.MODULES, name
        return (self.exe, name, *args)

    def process(
        self,
        module: ModuleType,
        *args: str,
        description: str,
        input_digest: Digest = EMPTY_DIGEST,
        env: Mapping[str, str] | None = None,
        append_only_caches: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Process:
        """A process running `module.main` with `args` in a sandbox holding `input_digest`."""
        assert "HOME" not in (env or {}), "builders run without a HOME"
        return Process(
            argv=self.argv(module, *args),
            description=description,
            input_digest=input_digest,
            immutable_input_digests=self.immutable_input_digests,
            # The scie and pex record the cache's real path, so what they install outlives the sandbox.
            env={**(env or {}), "HOME": self.home, builder_main.CACHE_HOME: "1"},
            append_only_caches={**(append_only_caches or {}), _CACHE: f".{_CACHE}"},
            **kwargs,
        )


@rule(desc="Build oci-builder", level=LogLevel.DEBUG)
async def build_oci_builder(
    oci: OciSubsystem, system_binaries: SystemBinariesSubsystem.EnvironmentAware, platform: Platform
) -> OciBuilder:
    sources = await create_digest(sandboxed_modules(*_MODULES))
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
                    f"--entry-point={builder_main.__name__}:main",
                    # Pex otherwise names the building Python's minor version.
                    "--python-shebang=#!/usr/bin/env python3",
                    # Runs from an installed venv, so a warm start costs no more than plain Python
                    # (once its setup is shared; see `home`).
                    "--venv",
                    "--scie=eager",
                    "--scie-only",
                    # Its own Python, independent of the one running Pants, so a Pants upgrade
                    # doesn't change what the builders write (layer compression uses its zlib).
                    f"--scie-pbs-release={oci.builder_pbs_release}",
                    f"--scie-python-version={oci.builder_python_version}",
                ),
                additional_input_digest=sources,
                # Building the scie runs `ldd` to find the host's libc.
                extra_env={"PATH": ":".join(system_binaries.system_binary_paths)},
                output_directories=(_OUTPUT,),
                description="Build oci-builder",
                level=LogLevel.DEBUG,
            )
        )
    )
    digest = await remove_prefix(RemovePrefix(result.output_digest, _OUTPUT))
    if platform in (Platform.linux_x86_64, Platform.linux_arm64):
        return OciBuilder(digest, home=f"/proc/self/cwd/.{_CACHE}")
    # Elsewhere `{chroot}` keeps the sandbox's path out of the cache key, but each sandbox redoes the
    # scie's venv setup (a fraction of a second).
    return OciBuilder(digest)


def rules():
    return [*collect_rules(), *pex_cli.rules()]
