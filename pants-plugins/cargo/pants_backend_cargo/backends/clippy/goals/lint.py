"""`pants lint` with clippy: one `cargo clippy` per workspace (or loose package)."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.environments.target_types import EnvironmentField
from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.partitions import Partition, Partitions
from pants.engine.intrinsics import execute_process
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import FieldSet
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants_backend_cargo.backends.clippy.subsystem import ClippySubsystem
from pants_backend_cargo.goals.fmt import RootMetadata, root_of
from pants_backend_cargo.target_types import CargoPackageNameField, _CargoPackageMarker
from pants_backend_cargo.util_rules.cargo import CargoProcessRequest, manifest_path
from pants_backend_cargo.util_rules.platform import CargoPlatformRequest, cargo_platform
from pants_backend_cargo.util_rules.rustup import install_rust_toolchain
from pants_backend_cargo.util_rules.sandbox import CargoSourcesRequest, cargo_sources
from pants_backend_cargo.util_rules.workspace import CargoPackageMapping

from pants_backend_platforms.util_rules.platforms import KnownPlatforms


@dataclass(frozen=True)
class CargoClippyFieldSet(FieldSet):
    required_fields = (CargoPackageNameField, _CargoPackageMarker)

    environment: EnvironmentField


class CargoClippyRequest(LintTargetsRequest):
    field_set_type = CargoClippyFieldSet
    tool_subsystem = ClippySubsystem


@rule
async def partition_clippy(
    request: CargoClippyRequest.PartitionRequest[CargoClippyFieldSet],
    clippy: ClippySubsystem,
    mapping: CargoPackageMapping,
) -> Partitions[CargoClippyFieldSet, RootMetadata]:
    if clippy.skip:
        return Partitions()
    by_root: dict[RootMetadata, list[CargoClippyFieldSet]] = {}
    for field_set in request.field_sets:
        by_root.setdefault(root_of(mapping, field_set.address), []).append(field_set)
    return Partitions(Partition(tuple(fss), root) for root, fss in by_root.items())


@rule(desc="Lint with clippy", level=LogLevel.DEBUG)
async def run_clippy(
    request: CargoClippyRequest.Batch[CargoClippyFieldSet, RootMetadata],
    clippy: ClippySubsystem,
    known: KnownPlatforms,
) -> LintResult:
    root = request.partition_metadata
    toolchain, cargo, sources = await concurrently(
        install_rust_toolchain(**implicitly()),
        cargo_platform(CargoPlatformRequest(known.host)),
        cargo_sources(
            CargoSourcesRequest(
                frozenset([root.root]),
                include_packages=True,
                platform=known.host,
                dependent=root.root,
            )
        ),
    )
    result = await execute_process(
        **implicitly(
            CargoProcessRequest(
                toolchain,
                (
                    "clippy",
                    "--locked",
                    f"--manifest-path={manifest_path(root.root.spec_path)}",
                    *(("--workspace",) if root.workspace else ()),
                    *clippy.args,
                ),
                description=f"cargo clippy {root.root}",
                digest=sources.digest,
                env=FrozenDict({**sources.env, **cargo.env}),
                level=LogLevel.INFO,
            )
        )
    )
    return LintResult.create(request, result)


def rules():
    return [*collect_rules(), *CargoClippyRequest.rules()]
