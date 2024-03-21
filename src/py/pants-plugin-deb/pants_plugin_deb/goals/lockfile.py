import itertools
import os
from dataclasses import dataclass
from operator import itemgetter

from pants.engine.internals.synthetic_targets import SyntheticAddressMaps, SyntheticTargetsRequest
from pants.engine.internals.target_adaptor import TargetAdaptor
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.unions import UnionRule

from pants_plugin_deb.subsystem import DebianPackagesSubsystem


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
    )
