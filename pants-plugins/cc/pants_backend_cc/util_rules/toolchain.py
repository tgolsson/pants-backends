from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BashBinary,
    BinaryNotFoundError,
    BinaryPath,
    BinaryPathRequest,
    BinaryPathTest,
    SystemBinariesSubsystem,
    find_binary,
)
from pants.engine.addresses import Address
from pants.engine.process import Process, ProcessCacheScope, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
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
from pants_backend_platforms.util_rules.lookup import TargetsOfAliasRequest, targets_of_alias
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


@dataclass(frozen=True)
class FoundCcToolchain:
    """The tools that build for a platform on this host, or why there are none."""

    address: Address | None
    tools: tuple[BinaryPath, BinaryPath, BinaryPath] | None
    """`cc`, `cxx` and `ar`; `None` with `error`."""
    search_path: tuple[str, ...]
    error: str | None
    unmatched: bool = False
    """The error is only that no `cc_toolchain` is defined for this host and platform: the usual
    reason a host cannot build for one, not a mistake."""


@rule(desc="Find the C toolchain", level=LogLevel.DEBUG)
async def find_cc_toolchain(
    request: CcToolchainRequest,
    known: KnownPlatforms,
    subsystem: CcToolchainsSubsystem,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> FoundCcToolchain:
    """`resolve_cc_toolchain` without raising, so callers can skip a platform this host cannot
    build for."""
    search_path = tuple(system_binaries.system_binary_paths)
    toolchains = await targets_of_alias(TargetsOfAliasRequest(CcToolchainTarget.alias), **implicitly())
    for tgt in toolchains:
        for field in (CcToolchainHostField, CcToolchainTargetField):
            if tgt[field].value not in known.by_name:
                error = known.unknown(tgt[field].value, f"the `{field.alias}` field of {tgt.address}")
                return FoundCcToolchain(None, None, search_path, error)

    matches = [
        t
        for t in toolchains
        if t[CcToolchainHostField].value == known.host.name
        and t[CcToolchainTargetField].value == request.platform.name
    ]
    native = request.platform == known.host
    strict = " `[cc-toolchains].strict` requires one for native builds too." if native else ""
    address: Address | None = None
    if len(matches) == 1:
        address = matches[0].address
        specs = (
            matches[0][CcToolchainCcField].value,
            matches[0][CcToolchainCxxField].value,
            matches[0][CcToolchainArField].value,
        )
    elif native and not matches and not subsystem.strict:
        specs = _IMPLICIT_NATIVE
    else:
        if toolchains:
            pairs = sorted(
                f"{t[CcToolchainHostField].value} -> {t[CcToolchainTargetField].value}" for t in toolchains
            )
            found = sorted(str(t.address) for t in matches) or "none"
            error = (
                f"Expected one `cc_toolchain` with host={known.host.name!r} and "
                f"target={request.platform.name!r}, found {found}. Defined: {pairs}.{strict}"
            )
        else:
            error = (
                f"Building for {request.platform.name} on {known.host.name} needs a `cc_toolchain` "
                f"with host={known.host.name!r} and target={request.platform.name!r}.{strict}"
            )
        # Without a match, only a native build is a mistake: `strict` asked for its toolchain.
        return FoundCcToolchain(None, None, search_path, error, unmatched=not matches and not native)

    try:
        requests = [
            binary_request(spec, search_path, version=version)
            for spec, version in zip(specs, (True, True, False))
        ]
    except ValueError as e:
        return FoundCcToolchain(address, None, search_path, str(e))
    found_paths = await concurrently(find_binary(r, **implicitly()) for r in requests)
    rationale = f"build for {request.platform.name}" + (f" ({address})" if address else "")
    tools = []
    for r, paths in zip(requests, found_paths):
        if paths.first_path is None:
            error = str(BinaryNotFoundError.from_request(r, rationale=rationale))
            return FoundCcToolchain(address, None, search_path, error)
        tools.append(paths.first_path)
    cc, cxx, ar = tools
    return FoundCcToolchain(address, (cc, cxx, ar), search_path, None)


@rule(desc="Resolve the C toolchain", level=LogLevel.DEBUG)
async def resolve_cc_toolchain(request: CcToolchainRequest, bash: BashBinary) -> CcToolchain:
    found = await find_cc_toolchain(request, **implicitly())
    if found.tools is None:
        raise ValueError(found.error)
    address, search_path, (cc, cxx, ar) = found.address, found.search_path, found.tools
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
