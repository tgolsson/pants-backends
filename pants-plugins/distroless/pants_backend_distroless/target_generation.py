from __future__ import annotations

import json
import os

from pants.core.target_types import FileSourceField
from pants.engine.download_file import download_file
from pants.engine.fs import (
    CreateDigest,
    DownloadFile,
    FileDigest,
    FileEntry,
    GlobMatchErrorBehavior,
    PathGlobs,
)
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import digest_to_snapshot, get_digest_contents, path_globs_to_digest
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    Dependencies,
    GeneratedSources,
    GeneratedTargets,
    GenerateSourcesRequest,
    GenerateTargetsRequest,
    InvalidFieldException,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.docutil import bin_name
from pants.util.logging import LogLevel
from pants.util.strutil import softwrap

from pants_backend_distroless import apt_resolver
from pants_backend_distroless.subsystem import AptSubsystem
from pants_backend_distroless.target_types import (
    DebPackageArchitectureField,
    DebPackageGroupTarget,
    DebPackagePackageField,
    DebPackagesArchitecturesField,
    DebPackagesExcludeField,
    DebPackageSourcesField,
    DebPackagesPackagesField,
    DebPackagesResolveField,
    DebPackagesSourcesField,
    DebPackagesTargetGenerator,
    DebPackageTarget,
)


class DebPackagesLockfileError(Exception):
    """A `deb_packages` lockfile is missing, out of date or in an unsupported format."""


class GenerateFromDebPackagesRequest(GenerateTargetsRequest):
    generate_from = DebPackagesTargetGenerator


def resolve_inputs(generator: DebPackagesTargetGenerator) -> dict:
    """The resolve inputs of a `deb_packages` target, as they're recorded in its lockfile."""
    return apt_resolver.normalize_inputs(
        sources=generator[DebPackagesSourcesField].value or (),
        architectures=generator[DebPackagesArchitecturesField].value or (),
        packages=generator[DebPackagesPackagesField].value or (),
        exclude=generator[DebPackagesExcludeField].value or (),
    )


def lockfile_path(generator: DebPackagesTargetGenerator, subsystem: AptSubsystem) -> str:
    resolve = generator[DebPackagesResolveField].value
    path = subsystem.resolves.get(resolve)
    if path is None:
        raise InvalidFieldException(
            f"The `{DebPackagesResolveField.alias}` field of {generator.address} is {resolve!r}, which "
            f"isn't a key of `[{subsystem.options_scope}].resolves`. Known resolves: "
            f"{', '.join(sorted(subsystem.resolves)) or 'none'}"
        )
    return path


@rule(desc="Generate targets from a `deb_packages` target")
async def generate_from_deb_packages(
    request: GenerateFromDebPackagesRequest,
    subsystem: AptSubsystem,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    # Like Python requirements, the targets are only the requested packages, from the BUILD file.
    # The lockfile is read when their `.deb` files are generated, so a missing or stale lockfile
    # fails only what uses them, and `generate-lockfiles` can resolve this target without one.
    generator = request.generator
    lockfile_path(generator, subsystem)
    packages = sorted(set(generator[DebPackagesPackagesField].value or ()))

    def address(name: str):
        return request.template_address.create_generated(name)

    generated = []
    for arch in sorted(set(generator[DebPackagesArchitecturesField].value or ())):
        for package in packages:
            generated.append(
                DebPackageTarget(
                    {
                        **request.template,
                        DebPackageArchitectureField.alias: arch,
                        DebPackagePackageField.alias: package,
                    },
                    address(f"{arch}/{package}"),
                    union_membership,
                )
            )
        generated.append(
            DebPackageGroupTarget(
                {
                    **request.template,
                    Dependencies.alias: [address(f"{arch}/{package}").spec for package in packages],
                },
                address(arch),
                union_membership,
            )
        )
    return GeneratedTargets(generator, generated)


def locked_entries(lockfile: dict, arch: str, package: str, directory: str) -> list[tuple[str, FileEntry]]:
    """The URL and file entry of each `.deb` in the locked closure of a requested package.

    Each file goes where a `file` target with an `http_source` in `directory` would put it. Raises
    `KeyError`, `TypeError` or `ValueError` if the lockfile doesn't lock the package consistently.
    """
    locked = lockfile["architectures"][arch]
    by_name = {p["name"]: p for p in locked["packages"]}
    root = by_name[locked["roots"][package]]
    return [
        (
            p["url"],
            FileEntry(
                os.path.join(directory, os.path.basename(p["url"])), FileDigest(p["sha256"], p["size"])
            ),
        )
        for p in [root, *(by_name[name] for name in root["closure"])]
    ]


class GenerateDebFilesRequest(GenerateSourcesRequest):
    input = DebPackageSourcesField
    output = FileSourceField


@rule(desc="Download the locked .deb files of a Debian package", level=LogLevel.DEBUG)
async def generate_deb_files(request: GenerateDebFilesRequest, subsystem: AptSubsystem) -> GeneratedSources:
    target = request.protocol_target
    wrapped = await resolve_target(
        WrappedTargetRequest(
            target.address.maybe_convert_to_target_generator(),
            description_of_origin=f"the `deb_packages` target of {target.address}",
        ),
        **implicitly(),
    )
    generator = wrapped.target
    resolve = generator[DebPackagesResolveField].value
    path = lockfile_path(generator, subsystem)

    digest = await path_globs_to_digest(
        PathGlobs([path], glob_match_error_behavior=GlobMatchErrorBehavior.ignore)
    )
    contents = await get_digest_contents(digest)
    lockfile = json.loads(contents[0].content) if contents else None

    problem = None
    if lockfile is None:
        problem = "doesn't exist"
    elif lockfile.get("version") != apt_resolver.LOCKFILE_VERSION:
        problem = f"has an unsupported version {lockfile.get('version')!r}"
    elif lockfile.get("inputs") != resolve_inputs(generator):
        problem = (
            "is out of date: it was generated for different `sources`, `architectures`, `packages` "
            "or `exclude`"
        )
    else:
        try:
            entries = locked_entries(
                lockfile,
                target[DebPackageArchitectureField].value,
                target[DebPackagePackageField].value,
                target.address.spec_path,
            )
        except (KeyError, TypeError, ValueError):
            problem = (
                f"is malformed: it has no consistent closure of `{target[DebPackagePackageField].value}`"
            )
    if problem is not None:
        raise DebPackagesLockfileError(
            softwrap(f"""
            The lockfile {path} for the `{resolve}` resolve of {generator.address} {problem}. Run
            `{bin_name()} generate-lockfiles --resolve={resolve}` to generate it.
            """)
        )

    await concurrently(
        download_file(DownloadFile(url, entry.file_digest), **implicitly()) for url, entry in entries
    )
    return GeneratedSources(
        await digest_to_snapshot(**implicitly(CreateDigest(entry for _, entry in entries)))
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromDebPackagesRequest),
        UnionRule(GenerateSourcesRequest, GenerateDebFilesRequest),
    ]
