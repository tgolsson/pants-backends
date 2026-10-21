from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from pants.core.goals.generate_lockfiles import (
    GenerateLockfile,
    GenerateLockfileResult,
    KnownUserResolveNames,
    KnownUserResolveNamesRequest,
    RequestedUserResolveNames,
    UserGenerateLockfiles,
)
from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.engine.fs import CreateDigest, FileContent
from pants.engine.internals.graph import find_all_unexpanded_targets
from pants.engine.intrinsics import create_digest
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_distroless import apt_resolver
from pants_backend_distroless.subsystem import AptSubsystem
from pants_backend_distroless.target_generation import lockfile_path, resolve_inputs
from pants_backend_distroless.target_types import (
    DebPackagesResolveField,
    DebPackagesTargetGenerator,
)

_RESOLVER_SOURCE = f"{apt_resolver.__name__.rpartition('.')[2]}.py"
_RESOLVER_MODULE = "__apt_resolver"
_RESOLVER_MAIN = f"import sys, {_RESOLVER_MODULE}; sys.exit({_RESOLVER_MODULE}.main(sys.argv[1:]))"


@dataclass(frozen=True)
class GenerateDebPackagesLockfile(GenerateLockfile):
    inputs_json: str


class KnownDebPackagesUserResolveNamesRequest(KnownUserResolveNamesRequest):
    pass


class RequestedDebPackagesUserResolveNames(RequestedUserResolveNames):
    pass


@rule
async def determine_deb_packages_user_resolves(
    _: KnownDebPackagesUserResolveNamesRequest, subsystem: AptSubsystem
) -> KnownUserResolveNames:
    return KnownUserResolveNames(
        names=tuple(subsystem.resolves),
        option_name=f"[{subsystem.options_scope}].resolves",
        requested_resolve_names_cls=RequestedDebPackagesUserResolveNames,
    )


@rule
async def setup_deb_packages_lockfile_requests(
    requested: RequestedDebPackagesUserResolveNames, subsystem: AptSubsystem
) -> UserGenerateLockfiles:
    # Unexpanded, since expanding a `deb_packages` target needs the lockfile being generated.
    targets = await find_all_unexpanded_targets()
    by_resolve: dict[str, list[DebPackagesTargetGenerator]] = {}
    for tgt in targets:
        if isinstance(tgt, DebPackagesTargetGenerator):
            by_resolve.setdefault(tgt[DebPackagesResolveField].value, []).append(tgt)

    requests = []
    for resolve in requested:
        generators = by_resolve.get(resolve, [])
        if len(generators) != 1:
            raise ValueError(
                f"The `{resolve}` resolve of `[{subsystem.options_scope}].resolves` must be declared by "
                f"exactly one `{DebPackagesTargetGenerator.alias}` target, found "
                f"{', '.join(str(g.address) for g in generators) or 'none'}"
            )
        (generator,) = generators
        requests.append(
            GenerateDebPackagesLockfile(
                resolve_name=resolve,
                lockfile_dest=lockfile_path(generator, subsystem),
                diff=False,
                inputs_json=json.dumps(resolve_inputs(generator), sort_keys=True),
            )
        )
    return UserGenerateLockfiles(requests)


@rule(desc="Resolve Debian packages", level=LogLevel.DEBUG)
async def generate_deb_packages_lockfile(
    request: GenerateDebPackagesLockfile, python: PythonBuildStandaloneBinary
) -> GenerateLockfileResult:
    # Read through `importlib.resources` so this also works when the plugin is imported from a zip.
    resolver_source = resources.files(__package__).joinpath(_RESOLVER_SOURCE).read_bytes()
    input_digest = await create_digest(
        CreateDigest(
            [
                FileContent(f"{_RESOLVER_MODULE}.py", resolver_source),
                FileContent("request.json", request.inputs_json.encode()),
            ]
        )
    )

    # The sources are pinned to snapshots, so caching the result like any other process is correct.
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(
                    python.path,
                    "-c",
                    _RESOLVER_MAIN,
                    "--request",
                    "request.json",
                    "--output",
                    request.lockfile_dest,
                ),
                input_digest=input_digest,
                output_files=(request.lockfile_dest,),
                description=f"Resolve Debian packages for the `{request.resolve_name}` resolve",
                level=LogLevel.DEBUG,
            )
        )
    )
    return GenerateLockfileResult(result.output_digest, request.resolve_name, request.lockfile_dest)


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateLockfile, GenerateDebPackagesLockfile),
        UnionRule(KnownUserResolveNamesRequest, KnownDebPackagesUserResolveNamesRequest),
        UnionRule(RequestedUserResolveNames, RequestedDebPackagesUserResolveNames),
    ]
