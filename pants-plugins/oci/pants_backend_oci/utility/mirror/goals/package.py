from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, OutputPathField, PackageFieldSet
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.utility.mirror.targets import (
    DestinationRepository,
    ImageDigest,
    ImageTag,
    SourceRepository,
)


@dataclass(frozen=True)
class MirrorImageFieldSet(PackageFieldSet):
    required_fields = (SourceRepository,)

    source: SourceRepository
    destination: DestinationRepository
    tag: ImageTag

    output_path: OutputPathField

    digest: ImageDigest


@rule(desc="Noop package", level=LogLevel.DEBUG)
async def package_nothing(field_set: MirrorImageFieldSet) -> BuiltPackage:
    return BuiltPackage(
        digest=EMPTY_DIGEST,
        artifacts=tuple(),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, MirrorImageFieldSet),
    ]
