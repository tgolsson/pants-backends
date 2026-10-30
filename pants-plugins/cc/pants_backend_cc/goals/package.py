"""`pants package` for `cc_binary`: writes the executable `util_rules/executable.py` builds to
`output_path`. What a shared library it links against needs at run time is not written."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.package import (
    BuiltPackage,
    BuiltPackageArtifact,
    OutputPathField,
    PackageFieldSet,
)
from pants.engine.fs import CreateDigest, FileEntry
from pants.engine.intrinsics import create_digest, get_digest_entries
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule

from pants_backend_cc.target_types import CcBinarySourcesField
from pants_backend_cc.util_rules.executable import (
    BINARY_OUTPUT_DIR,
    ExecutableBuildError,
    ExecutableBuildRequest,
    build_executable,
)


@dataclass(frozen=True)
class CcBinaryFieldSet(PackageFieldSet):
    required_fields = (CcBinarySourcesField, OutputPathField)

    sources: CcBinarySourcesField
    output_path: OutputPathField


@rule(desc="Package C/C++ executable")
async def package_cc_binary(field_set: CcBinaryFieldSet) -> BuiltPackage:
    address = field_set.address
    built = await build_executable(
        ExecutableBuildRequest(address, f"{BINARY_OUTPUT_DIR}/{address.path_safe_spec}"), **implicitly()
    )
    entries = await get_digest_entries(built.runtime)
    executable = next((e for e in entries if isinstance(e, FileEntry) and e.path == built.executable), None)
    if executable is None:
        raise ExecutableBuildError(f"{address}: the link wrote no executable at `{built.executable}`")
    output_path = field_set.output_path.value_or_default(file_ending=None)
    digest = await create_digest(
        CreateDigest([FileEntry(output_path, executable.file_digest, is_executable=True)])
    )
    return BuiltPackage(digest, (BuiltPackageArtifact(output_path),))


def rules():
    return [*collect_rules(), UnionRule(PackageFieldSet, CcBinaryFieldSet)]
