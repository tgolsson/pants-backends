from __future__ import annotations

import json
import logging
import os

from pants.core.target_types import http_source
from pants.engine.fs import GlobMatchErrorBehavior, PathGlobs
from pants.engine.intrinsics import get_digest_contents, path_globs_to_digest
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    Dependencies,
    GeneratedTargets,
    GenerateTargetsRequest,
    InvalidFieldException,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.docutil import bin_name
from pants.util.strutil import softwrap

from pants_backend_distroless import apt_resolver
from pants_backend_distroless.subsystem import AptSubsystem
from pants_backend_distroless.target_types import (
    DebPackageDependenciesField,
    DebPackageGroupTarget,
    DebPackagesArchitecturesField,
    DebPackagesExcludeField,
    DebPackageSourceField,
    DebPackagesPackagesField,
    DebPackagesResolveField,
    DebPackagesSourcesField,
    DebPackagesTargetGenerator,
    DebPackageTarget,
)

logger = logging.getLogger(__name__)


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


@rule(desc="Generate targets from a Debian package lockfile")
async def generate_from_deb_packages(
    request: GenerateFromDebPackagesRequest,
    subsystem: AptSubsystem,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    generator = request.generator
    resolve = generator[DebPackagesResolveField].value
    path = lockfile_path(generator, subsystem)

    digest = await path_globs_to_digest(
        PathGlobs([path], glob_match_error_behavior=GlobMatchErrorBehavior.ignore)
    )
    contents = await get_digest_contents(digest)
    lockfile = json.loads(contents[0].content) if contents else None

    # `generate-lockfiles` expands every target, so a missing or stale lockfile can't be an error
    # here. Generating nothing instead fails any use of the packages, after this warning.
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
    if problem is not None:
        logger.warning(
            softwrap(f"""
            The lockfile {path} for the `{resolve}` resolve of {generator.address} {problem}, so
            no packages are generated from it. Run
            `{bin_name()} generate-lockfiles --resolve={resolve}` to update it.
            """)
        )
        return GeneratedTargets(generator, ())

    def address(name: str):
        return request.template_address.create_generated(name)

    generated = []
    for arch, locked in sorted(lockfile["architectures"].items()):
        for package in locked["packages"]:
            generated.append(
                DebPackageTarget(
                    {
                        **request.template,
                        DebPackageSourceField.alias: http_source(
                            package["url"],
                            len=package["size"],
                            sha256=package["sha256"],
                            filename=os.path.basename(package["url"]),
                        ),
                        DebPackageDependenciesField.alias: [
                            address(f"{arch}/{dep}").spec for dep in package["depends"]
                        ],
                    },
                    address(f"{arch}/{package['name']}"),
                    union_membership,
                )
            )
        generated.append(
            DebPackageGroupTarget(
                {
                    **request.template,
                    Dependencies.alias: [address(f"{arch}/{root}").spec for root in locked["roots"]],
                },
                address(arch),
                union_membership,
            )
        )

    return GeneratedTargets(generator, generated)


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromDebPackagesRequest),
    ]
