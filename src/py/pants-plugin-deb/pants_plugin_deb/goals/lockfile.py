import itertools
import os
from collections import defaultdict
from dataclasses import dataclass
from operator import itemgetter

from pants.core.goals.generate_lockfiles import (
    GenerateLockfile,
    GenerateLockfileResult,
    GenerateLockfilesSubsystem,
    KnownUserResolveNames,
    KnownUserResolveNamesRequest,
    RequestedUserResolveNames,
    UserGenerateLockfiles,
    WrappedGenerateLockfile,
)
from pants.engine.fs import CreateDigest, Digest, FileContent
from pants.engine.internals.synthetic_targets import SyntheticAddressMaps, SyntheticTargetsRequest
from pants.engine.internals.target_adaptor import TargetAdaptor
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import AllTargets
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants.util.ordered_set import FrozenOrderedSet

from pants_plugin_deb.subsystem import DebianPackagesSubsystem
from pants_plugin_deb.targets import DebianPackageField, DebianResolveField


@dataclass
class DebianLockfileConfig:
    lockfile_dest: str
    resolve_name: str


@dataclass(frozen=True)
class DebianLockfile:
    pass


@rule
def generate_debian_lockfile_impl(request: DebianLockfileConfig) -> DebianLockfile:
    pass


@dataclass(frozen=True)
class GenerateDebianLockfile(GenerateLockfile):
    packages: FrozenOrderedSet[str]


@rule
def wrap_debian_lockfile_request(request: GenerateDebianLockfile) -> WrappedGenerateLockfile:
    return WrappedGenerateLockfile(request)


@rule(desc="Generate Debian lockfile", level=LogLevel.DEBUG)
async def generate_debian_lockfile(
    request: GenerateDebianLockfile,
    generate_lockfiles_subsystem: GenerateLockfilesSubsystem,
) -> GenerateLockfileResult:
    resolved_lockfile = await Get(DebianLockfile, DebianLockfileConfig, request.config)
    regenerate_command = (
        generate_lockfiles_subsystem.custom_command or f"{bin_name()} generate-lockfiles"
    )

    resolved_lockfile_contents = resolved_lockfile

    lockfile_digest = await Get(
        Digest,
        CreateDigest([FileContent(request.lockfile_dest, resolved_lockfile_contents)]),
    )

    return GenerateLockfileResult(lockfile_digest, request.resolve_name, request.lockfile_dest)


class RequestedDebianUserResolveNames(RequestedUserResolveNames):
    pass


class KnownDebianUserResolveNamesRequest(KnownUserResolveNamesRequest):
    pass


@rule
def determine_debian_user_resolves(
    _: KnownDebianUserResolveNamesRequest, debian_subsystem: DebianPackagesSubsystem
) -> KnownUserResolveNames:
    return KnownUserResolveNames(
        names=tuple(debian_subsystem.resolves.keys()),
        option_name=f"[{debian_subsystem.options_scope}].resolves",
        requested_resolve_names_cls=RequestedDebianUserResolveNames,
    )


@rule
async def setup_user_lockfile_requests(
    requested: RequestedDebianUserResolveNames,
    all_targets: AllTargets,
    debian_subsystem: DebianPackagesSubsystem,
) -> UserGenerateLockfiles:
    resolve_to_packages = defaultdict(list)
    for tgt in sorted(all_targets, key=lambda t: t.address):
        if not tgt.has_field(DebianResolveField):
            continue

        resolve = tgt.get(DebianResolveField)
        package = tgt.get(DebianPackageField)
        resolve_to_packages[resolve.value].append(package.value)

    return [
        GenerateDebianLockfile(
            resolve_name=resolve,
            lockfile_dest=debian_subsystem.resolves[resolve],
            diff=False,
            packages=FrozenOrderedSet(packages),
        )
        for resolve, packages in resolve_to_packages.items()
        if resolve in requested
    ]


@dataclass(frozen=True)
class DebianSyntheticLockfileTargetsRequest(SyntheticTargetsRequest):
    """Register the type used to create synthetic targets for Debian lockfiles.

    As the paths for all lockfiles are known up-front, we set the `path` field to
    `SyntheticTargetsRequest.SINGLE_REQUEST_FOR_ALL_TARGETS` so that we get a single request for all
    our synthetic targets rather than one request per directory.
    """

    path: str = SyntheticTargetsRequest.SINGLE_REQUEST_FOR_ALL_TARGETS


def synthetic_lockfile_target_name(resolve: str) -> str:
    return f"_{resolve}_lockfile"


@rule
async def debian_lockfile_synthetic_targets(
    request: DebianSyntheticLockfileTargetsRequest,
    debian: DebianPackagesSubsystem,
) -> SyntheticAddressMaps:
    resolves = [
        (os.path.dirname(lockfile), os.path.basename(lockfile), name)
        for name, lockfile in debian.resolves.items()
    ]

    return SyntheticAddressMaps.for_targets_request(
        request,
        [
            (
                os.path.join(spec_path, "BUILD.debian-lockfiles"),
                tuple(
                    TargetAdaptor(
                        "_lockfiles",
                        name=synthetic_lockfile_target_name(name),
                        sources=[lockfile],
                        __description_of_origin__=f"the [debian-packages].resolves option {name!r}",
                    )
                    for _, lockfile, name in lockfiles
                ),
            )
            for spec_path, lockfiles in itertools.groupby(sorted(resolves), key=itemgetter(0))
        ],
    )


def rules():
    return (
        *collect_rules(),
        UnionRule(SyntheticTargetsRequest, DebianSyntheticLockfileTargetsRequest),
        UnionRule(GenerateLockfile, GenerateDebianLockfile),
        UnionRule(KnownUserResolveNamesRequest, KnownDebianUserResolveNamesRequest),
        UnionRule(RequestedUserResolveNames, RequestedDebianUserResolveNames),
    )
