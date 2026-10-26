"""Docker as the runner for `pants run`, OCI repl and scripted build steps, with `[oci].runner =
"docker"`.

Docker is found and configured by Pants' Docker backend, `pants.backend.docker`, through its
`[docker]` options. `docker_runner` and `docker_builder` do the work; these rules only decide what
they run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

from pants.backend.docker.subsystems.docker_options import DockerOptions
from pants.backend.docker.util_rules.docker_binary import DockerBinary
from pants.core.util_rules.env_vars import environment_vars_subset
from pants.core.util_rules.system_binaries import SystemBinariesSubsystem
from pants.engine.env_vars import EnvironmentVarsRequest
from pants.engine.process import Process, ProcessResult, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.goals.package import OciArchiveRequest, package_oci_archive
from pants_backend_oci.subsystem import OciRunner
from pants_backend_oci.util_rules import docker_builder, docker_runner
from pants_backend_oci.util_rules.runner import ContainerBuildRequest, ContainerRunRequest
from pants_backend_oci.util_rules.sandboxed import OciBuilder


@dataclass(frozen=True)
class DockerBuildRequest(ContainerBuildRequest):
    """Run `docker_builder` with `args` on `bundle`'s layout."""

    runner = OciRunner.docker


@rule
async def docker_build(
    request: DockerBuildRequest,
    docker: DockerBinary,
    docker_options: DockerOptions.EnvironmentAware,
    builder: OciBuilder,
    system_binaries: SystemBinariesSubsystem.EnvironmentAware,
) -> ProcessResult:
    env = dict(await environment_vars_subset(EnvironmentVarsRequest(docker_options.env_vars), **implicitly()))
    # The builder's HOME points its scie at a cache, so a configured one goes by another name.
    if "HOME" in env:
        env[docker_builder.DOCKER_HOME] = env.pop("HOME")
    process = builder.process(
        docker_builder,
        "--docker",
        docker.path,
        *request.args,
        input_digest=request.bundle.digest,
        # The Docker backend's own environment: `[docker].env_vars`, and its `[docker].tools` on the
        # PATH ahead of the system binaries.
        env={
            **docker._get_process_environment({"PATH": ":".join(system_binaries.system_binary_paths), **env}),
            # Unread: it puts the docker binary in the cache key, so an upgrade reruns build steps.
            "__OCI_DOCKER_FINGERPRINT": docker.fingerprint or "",
        },
        immutable_input_digests=docker.extra_input_digests,
        output_files=request.output_files,
        output_directories=request.output_directories,
        description=request.description,
        level=LogLevel.DEBUG,
    )
    return await fallible_to_exec_result_or_raise(**implicitly({process: Process}))


@dataclass(frozen=True)
class DockerRunRequest(ContainerRunRequest):
    runner = OciRunner.docker


@rule
async def docker_run(
    request: DockerRunRequest,
    docker: DockerBinary,
    options: DockerOptions,
    options_env_aware: DockerOptions.EnvironmentAware,
    builder: OciBuilder,
) -> Process:
    # Named by its digest, as scripted build steps name what they load.
    name = f"pants-{request.bundle.image_sha.partition(':')[2]}:latest"
    archive_process = await package_oci_archive(
        OciArchiveRequest(request.bundle.digest, "image.tar", name, f"Archive {request.address} for docker"),
        **implicitly(),
    )
    archive = await fallible_to_exec_result_or_raise(**implicitly({archive_process: Process}))
    env = await environment_vars_subset(EnvironmentVarsRequest(options_env_aware.env_vars), **implicitly())
    run_args = ["--rm"]
    if request.interactive:
        run_args.append("--interactive")
    if request.tty:
        run_args.append("--tty")
    run = docker.run_image(name, docker_run_args=(*run_args, *options.run_args), env=env)
    exe, *runner_args = builder.argv(
        docker_runner, "--archive", "{chroot}/image.tar", "--run", json.dumps(run.argv)
    )
    # The run happens outside the sandbox, so the builder is addressed through `{chroot}`.
    return Process(
        [f"{{chroot}}/{exe}", *runner_args, "--"],
        input_digest=archive.output_digest,
        immutable_input_digests={**(run.immutable_input_digests or {}), **builder.immutable_input_digests},
        env=_in_chroot(run.env),
        description=f"Running {request.address}",
    )


def _in_chroot(env: Mapping[str, str]) -> dict[str, str]:
    """`env` for a run outside the sandbox: `[docker].tools` are on the PATH relative to it."""
    if "PATH" not in env:
        return dict(env)
    path = [p if os.path.isabs(p) else f"{{chroot}}/{p}" for p in env["PATH"].split(os.pathsep)]
    return {**env, "PATH": os.pathsep.join(path)}


def rules():
    return [
        *collect_rules(),
        UnionRule(ContainerBuildRequest, DockerBuildRequest),
        UnionRule(ContainerRunRequest, DockerRunRequest),
    ]
