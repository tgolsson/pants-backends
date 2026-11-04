"""`pants run` of a `cargo_binary`: the binary built for the host, with the `files`/`resources`,
staged packages and `env` of its closure; no cargo sources."""

from __future__ import annotations

import os.path

from pants.core.goals.run import RunRequest
from pants.engine.fs import MergeDigests
from pants.engine.intrinsics import merge_digests
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants_backend_cargo.goals.package import CargoBinaryFieldSet, build_cargo_binary_for_platform
from pants_backend_cargo.util_rules.sandbox import CargoSourcesRequest, cargo_sources

from pants_backend_platforms.util_rules.platforms import KnownPlatforms


@rule
async def create_cargo_binary_run_request(
    field_set: CargoBinaryFieldSet, known: KnownPlatforms
) -> RunRequest:
    built, sources = await concurrently(
        build_cargo_binary_for_platform(field_set, known.host, **implicitly()),
        cargo_sources(
            CargoSourcesRequest(
                frozenset([field_set.address]),
                include_packages=True,
                platform=known.host,
                dependent=field_set.address,
                include_cargo_sources=False,
            )
        ),
    )
    (relpath,) = built.files
    digest = await merge_digests(MergeDigests([built.digest, sources.digest]))
    return RunRequest(digest=digest, args=(os.path.join("{chroot}", relpath),), extra_env=sources.env)


def rules():
    return [*collect_rules(), *CargoBinaryFieldSet.rules()]
