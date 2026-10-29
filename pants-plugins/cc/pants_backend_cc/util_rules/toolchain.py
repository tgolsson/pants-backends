from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BashBinary,
    BinaryPath,
    BinaryPathRequest,
    BinaryPathTest,
    SystemBinariesSubsystem,
    find_binary,
)
from pants.engine.addresses import Address
from pants.engine.process import Process, ProcessCacheScope, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import AllTargets
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_cc.subsystems import CcToolchainsSubsystem
from pants_backend_cc.target_types import (
    CcToolchainArField,
    CcToolchainCcField,
    CcToolchainCxxField,
    CcToolchainHostField,
    CcToolchainTarget,
    CcToolchainTargetField,
)
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform

_IMPLICIT_NATIVE = ("gcc", "g++", "ar")

# The contents of the linker the compiler drives and of `ar`: their `--version` is not portable,
# and an upgrade changes the output without changing the compiler.
_BINUTILS_FINGERPRINT = """\
ld=$("$1" -print-prog-name=ld 2>/dev/null || true)
case "$ld" in ""|*/*) ;; *) ld=$(command -v "$ld" || true) ;; esac
for f in $ld "$2"; do if [ -f "$f" ]; then cksum "$f"; fi; done
"""


@dataclass(frozen=True)
class CcToolchainRequest:
    platform: TargetPlatform


@dataclass(frozen=True)
class CcToolchain:
    address: Address | None
    """The `cc_toolchain` target; `None` for the implicit native toolchain."""
    platform: TargetPlatform
    cc: BinaryPath
    cxx: BinaryPath
    ar: BinaryPath
    search_path: tuple[str, ...]
    binutils_fingerprint: str

    @property
    def env(self) -> FrozenDict[str, str]:
        """The tools' own directories come first, so a driver finds its assembler and linker
        rather than the host's."""
        tool_dirs = [os.path.dirname(t.path) for t in (self.cc, self.cxx, self.ar)]
        return FrozenDict(
            {
                "PATH": os.pathsep.join(dict.fromkeys((*tool_dirs, *self.search_path))),
                "__PANTS_CC_TOOLCHAIN": ",".join(
                    (self.cc.fingerprint, self.cxx.fingerprint, self.binutils_fingerprint)
                ),
            }
        )


def binary_request(spec: str, search_path: tuple[str, ...], *, version: bool) -> BinaryPathRequest:
    if os.path.isabs(spec):
        name, search_path = os.path.basename(spec), (os.path.dirname(spec),)
    elif "/" in spec:
        raise ValueError(f"`{spec}`: a tool is a name on the search path or an absolute path.")
    else:
        name = spec
    # Archivers such as BSD `ar` reject `--version`, and a failing probe means "not found".
    test = BinaryPathTest(args=["--version"]) if version else None
    return BinaryPathRequest(binary_name=name, search_path=search_path, test=test)


@rule(desc="Resolve the C toolchain", level=LogLevel.DEBUG)
async def resolve_cc_toolchain(
    request: CcToolchainRequest,
    known: KnownPlatforms,
    all_targets: AllTargets,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
    bash: BashBinary,
    subsystem: CcToolchainsSubsystem,
) -> CcToolchain:
    toolchains = [t for t in all_targets if isinstance(t, CcToolchainTarget)]
    for tgt in toolchains:
        for field in (CcToolchainHostField, CcToolchainTargetField):
            known.get(tgt[field].value, f"the `{field.alias}` field of {tgt.address}")

    address: Address | None = None
    matches = [
        t
        for t in toolchains
        if t[CcToolchainHostField].value == known.host.name
        and t[CcToolchainTargetField].value == request.platform.name
    ]
    native = request.platform == known.host
    strict = " `[cc-toolchains].strict` requires one for native builds too." if native else ""
    if len(matches) == 1:
        address = matches[0].address
        specs = tuple(
            matches[0][f].value for f in (CcToolchainCcField, CcToolchainCxxField, CcToolchainArField)
        )
    elif native and not matches and not subsystem.strict:
        specs = _IMPLICIT_NATIVE
    elif toolchains:
        pairs = sorted(
            f"{t[CcToolchainHostField].value} -> {t[CcToolchainTargetField].value}" for t in toolchains
        )
        found = sorted(str(t.address) for t in matches) or "none"
        raise ValueError(
            f"Expected one `cc_toolchain` with host={known.host.name!r} and "
            f"target={request.platform.name!r}, found {found}. Defined: {pairs}.{strict}"
        )
    else:
        raise ValueError(
            f"Building for {request.platform.name} on {known.host.name} needs a `cc_toolchain` "
            f"with host={known.host.name!r} and target={request.platform.name!r}.{strict}"
        )

    search_path = tuple(system_binaries.system_binary_paths)
    requests = [
        binary_request(spec, search_path, version=version)
        for spec, version in zip(specs, (True, True, False))
    ]
    found_paths = await concurrently(find_binary(r, **implicitly()) for r in requests)
    rationale = f"build for {request.platform.name}" + (f" ({address})" if address else "")
    cc, cxx, ar = (
        paths.first_path_or_raise(r, rationale=rationale) for r, paths in zip(requests, found_paths)
    )
    binutils = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(bash.path, "-c", _BINUTILS_FINGERPRINT, "fingerprint", cc.path, ar.path),
                env={"PATH": os.pathsep.join(search_path)},
                description=f"Fingerprint the binutils of {cc.path}",
                level=LogLevel.DEBUG,
                cache_scope=ProcessCacheScope.PER_SESSION,
            )
        )
    )
    fingerprint = BinaryPath.fingerprinted(ar.path, binutils.stdout).fingerprint
    return CcToolchain(address, request.platform, cc, cxx, ar, search_path, fingerprint)


def rules():
    return collect_rules()
