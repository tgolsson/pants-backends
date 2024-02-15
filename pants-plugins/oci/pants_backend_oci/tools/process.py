"""
Various tools for creating processes.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.fs import Digest, MergeDigests
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, rule


@dataclass(frozen=True)
class FusedProcess:
    processes: tuple(Process)


@rule
async def fuse_process(request: FusedProcess, bash: BashBinary) -> Process:
    common_output_directories = list(set(sum([list(p.output_directories) for p in request.processes], [])))
    immutable_input_digests = {}
    for p in request.processes:
        immutable_input_digests.update(p.immutable_input_digests)

    common_output_files = list(set(sum([list(p.output_files) for p in request.processes], [])))
    common_digest_input = list(set([p.input_digest for p in request.processes if p.input_digest]))
    common_description = " | ".join(p.description for p in request.processes)

    common_digest = await Get(Digest, MergeDigests(common_digest_input))

    env = {}
    for p in request.processes:
        env.update(p.env)

    script = """
    export ROOT_DIR="$(pwd)"
    export SANDBOX_DIR="{chroot}"
    cd $SANDBOX_DIR
    """ + "\n".join(
        " ".join(v if "# nosplit" in v else shlex.quote(v) for v in p.argv) for p in request.processes
    )

    return Process(
        argv=(bash.path, "-c", script, "$@"),
        description=f"Fused run of: {common_description}",
        input_digest=common_digest,
        output_files=common_output_files,
        output_directories=common_output_directories,
        immutable_input_digests=immutable_input_digests,
        env=env,
    )


def rules():
    return collect_rules()
