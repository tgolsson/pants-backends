"""`pants fmt` with rustfmt: one `cargo fmt` per workspace (or loose package)."""

from __future__ import annotations

import glob
from dataclasses import dataclass

from pants.core.environments.target_types import EnvironmentField
from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.partitions import Partition, PartitionerType, Partitions
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import CreateDigest, FileEntry, MergeDigests
from pants.engine.intrinsics import create_digest, get_digest_entries, merge_digests
from pants.engine.process import fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import FieldSet
from pants.util.logging import LogLevel
from pants_backend_cargo.subsystems import RustSubsystem
from pants_backend_cargo.target_types import CargoPackageNameField, CargoPackageSourcesField
from pants_backend_cargo.util_rules.cargo import CargoProcessRequest, manifest_path
from pants_backend_cargo.util_rules.rustup import install_rust_toolchain
from pants_backend_cargo.util_rules.sandbox import CargoSourcesRequest, cargo_sources
from pants_backend_cargo.util_rules.workspace import CargoPackageMapping

from pants_backend_platforms.util_rules.platforms import KnownPlatforms


@dataclass(frozen=True)
class CargoFmtFieldSet(FieldSet):
    required_fields = (CargoPackageSourcesField, CargoPackageNameField)

    sources: CargoPackageSourcesField
    environment: EnvironmentField


class CargoFmtRequest(FmtTargetsRequest):
    field_set_type = CargoFmtFieldSet
    partitioner_type = PartitionerType.CUSTOM
    tool_subsystem = RustSubsystem


@dataclass(frozen=True)
class RootMetadata:
    """A workspace (or a package outside any), the unit cargo fmt and clippy run on."""

    root: Address
    workspace: bool

    @property
    def description(self) -> str:
        return str(self.root)


def root_of(mapping: CargoPackageMapping, package: Address) -> RootMetadata:
    workspace = mapping.workspace_for(package)
    return RootMetadata(workspace or package, workspace is not None)


@rule
async def partition_cargo_fmt(
    request: CargoFmtRequest.PartitionRequest[CargoFmtFieldSet],
    rust: RustSubsystem,
    mapping: CargoPackageMapping,
) -> Partitions[str, RootMetadata]:
    if rust.skip:
        return Partitions()
    all_sources = await concurrently(
        determine_source_files(SourceFilesRequest([fs.sources])) for fs in request.field_sets
    )
    by_root: dict[RootMetadata, set[str]] = {}
    for field_set, sources in zip(request.field_sets, all_sources):
        files = by_root.setdefault(root_of(mapping, field_set.address), set())
        files.update(f for f in sources.files if f.endswith(".rs"))
    return Partitions(Partition(tuple(sorted(files)), root) for root, files in by_root.items() if files)


@rule(desc="Format with cargo fmt", level=LogLevel.DEBUG)
async def cargo_fmt(request: CargoFmtRequest.Batch[str, RootMetadata], known: KnownPlatforms) -> FmtResult:
    root = request.partition_metadata
    toolchain, sources = await concurrently(
        install_rust_toolchain(**implicitly()),
        cargo_sources(
            CargoSourcesRequest(
                frozenset([root.root]),
                include_packages=False,
                platform=known.host,
                dependent=root.root,
            )
        ),
    )
    # The batch's files come from the fmt chain (an earlier formatter may have changed them);
    # everything else cargo needs comes from the sources.
    # A file name may hold glob characters (`src/[gen].rs`): the batch's files leave the
    # sources by exact path, and their outputs are captured escaped.
    batch = set(request.files)
    entries = await get_digest_entries(sources.digest)
    others = await create_digest(
        CreateDigest(e for e in entries if not (isinstance(e, FileEntry) and e.path in batch))
    )
    input_digest = await merge_digests(MergeDigests([others, request.snapshot.digest]))
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            CargoProcessRequest(
                toolchain,
                (
                    "fmt",
                    f"--manifest-path={manifest_path(root.root.spec_path)}",
                    *(("--all",) if root.workspace else ()),
                ),
                description=f"cargo fmt {root.root}",
                digest=input_digest,
                output_files=tuple(glob.escape(f) for f in request.files),
                env=sources.env,
            )
        )
    )
    return await FmtResult.create(request, result)


def rules():
    return [*collect_rules(), *CargoFmtRequest.rules()]
