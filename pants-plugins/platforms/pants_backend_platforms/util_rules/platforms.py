from __future__ import annotations

from dataclasses import dataclass

from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, rule
from pants.engine.target import AllTargets
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_platforms.subsystems import PlatformsSubsystem
from pants_backend_platforms.target_types import (
    PlatformArchField,
    PlatformOsField,
    PlatformsField,
    PlatformTarget,
)

_MACHINES = {
    Platform.linux_x86_64: ("linux", "x86_64"),
    Platform.linux_arm64: ("linux", "aarch64"),
    Platform.macos_x86_64: ("macos", "x86_64"),
    Platform.macos_arm64: ("macos", "aarch64"),
}


@dataclass(frozen=True, order=True)
class TargetPlatform:
    name: str
    os: str
    arch: str


@dataclass(frozen=True)
class KnownPlatforms:
    by_name: FrozenDict[str, TargetPlatform]
    host: TargetPlatform

    def get(self, name: str, origin: str) -> TargetPlatform:
        platform = self.by_name.get(name)
        if platform is None:
            raise ValueError(f"{origin}: unknown platform `{name}`; known: {sorted(self.by_name)}")
        return platform

    def listed(self, field: PlatformsField) -> tuple[TargetPlatform, ...]:
        """The platforms a `platforms` field names, the host when unset."""
        origin = f"the `platforms` field of {field.address}"
        names = dict.fromkeys(field.value or (self.host.name,))
        return tuple(self.get(name, origin) for name in names)


@rule(desc="Find the platforms", level=LogLevel.DEBUG)
async def known_platforms(
    all_targets: AllTargets, platform: Platform, subsystem: PlatformsSubsystem
) -> KnownPlatforms:
    if platform not in _MACHINES:
        raise ValueError(f"pants_backend_platforms does not support Pants on {platform.value}.")
    os_, arch = _MACHINES[platform]
    by_name: dict[str, TargetPlatform] = {}
    for tgt in all_targets:
        if not isinstance(tgt, PlatformTarget):
            continue
        name = tgt.address.target_name
        if name in by_name:
            raise ValueError(f"Two `platform` targets are named `{name}`.")
        by_name[name] = TargetPlatform(name, tgt[PlatformOsField].value, tgt[PlatformArchField].value)

    if subsystem.host:
        host = by_name.get(subsystem.host)
        if host is None:
            raise ValueError(
                f"[platforms].host: unknown platform `{subsystem.host}`; known: {sorted(by_name)}"
            )
        if (host.os, host.arch) != (os_, arch):
            raise ValueError(
                f"[platforms].host is `{host.name}` ({host.os}, {host.arch}), but this machine is "
                f"({os_}, {arch})."
            )
        return KnownPlatforms(FrozenDict(by_name), host)

    matches = sorted(name for name, p in by_name.items() if (p.os, p.arch) == (os_, arch))
    if len(matches) > 1:
        raise ValueError(
            f"Several `platform` targets match this machine ({os_}, {arch}): {matches}. Choose "
            "one with `[platforms].host`."
        )
    if matches:
        return KnownPlatforms(FrozenDict(by_name), by_name[matches[0]])
    host = TargetPlatform(f"{os_}-{arch}", os_, arch)
    if host.name in by_name:
        raise ValueError(
            f"No `platform` matches this machine ({os_}, {arch}), and the implicit host's name "
            f"`{host.name}` is taken by another platform."
        )
    return KnownPlatforms(FrozenDict({**by_name, host.name: host}), host)


@dataclass(frozen=True)
class SelectPlatformsRequest:
    platforms: PlatformsField


@dataclass(frozen=True)
class SelectedPlatforms:
    platforms: tuple[TargetPlatform, ...]


@rule
async def select_platforms(
    request: SelectPlatformsRequest, known: KnownPlatforms, subsystem: PlatformsSubsystem
) -> SelectedPlatforms:
    enabled = {known.host}
    enabled.update(known.get(name, "[platforms].enabled") for name in subsystem.enabled)
    return SelectedPlatforms(tuple(p for p in known.listed(request.platforms) if p in enabled))


def rules():
    return collect_rules()
