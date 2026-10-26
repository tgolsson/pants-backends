""" """

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.repl import ReplImplementation, ReplRequest
from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior, RunRequest
from pants.engine.internals.graph import resolve_target
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import Target, WrappedTargetRequest
from pants.engine.unions import UnionMembership, UnionRule
from pants.option.global_options import NamedCachesDirOption

from pants_backend_oci.goals.package import OciArchiveRequest, package_oci_archive
from pants_backend_oci.subsystem import OciRunner, OciSubsystem
from pants_backend_oci.target_types import ImageRepository, ImageRunTty
from pants_backend_oci.util_rules import podman_runner
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.run import PODMAN_CACHE, PODMAN_STORE_VERSION, PodmanBinary
from pants_backend_oci.util_rules.runner import ContainerRunRequest, container_run, runner_member
from pants_backend_oci.util_rules.sandboxed import OciBuilder


@dataclass(frozen=True)
class RunImageBundleCommand(RunFieldSet):
    required_fields = (ImageRepository,)
    run_in_sandbox_behavior = RunInSandboxBehavior.RUN_REQUEST_NOT_HERMETIC

    repository: ImageRepository
    run_tty: ImageRunTty


@dataclass(frozen=True)
class RunImageBundleProcessRequest:
    target: Target

    interactive: bool = False


@dataclass(frozen=True)
class PodmanRunRequest(ContainerRunRequest):
    runner = OciRunner.podman


@rule
async def podman_run(
    request: PodmanRunRequest,
    podman: PodmanBinary,
    builder: OciBuilder,
    caches: NamedCachesDirOption,
    platform: Platform,
    oci: OciSubsystem,
) -> Process:
    # Local Linux podman reads the archive directly; elsewhere, or through a named connection, it's
    # loaded into the connection's store first, under a name only these runs use.
    local = not oci.podman_connection and platform in (Platform.linux_x86_64, Platform.linux_arm64)
    name = None if local else podman_runner.remote_ref(request.bundle.image_sha)
    # Only the image's own blobs go to podman, in an OCI archive.
    archive_process = await package_oci_archive(
        OciArchiveRequest(request.bundle.digest, "image.tar", name, f"Archive {request.address} for podman"),
        **implicitly(),
    )
    archive = await fallible_to_exec_result_or_raise(**implicitly({archive_process: Process}))
    remote = () if local else ("--connection", oci.podman_connection, "--digest", request.bundle.image_sha)
    exe, *runner_args = builder.argv(
        podman_runner,
        "--podman",
        podman.path,
        "--cache",
        str(caches.val / PODMAN_CACHE / PODMAN_STORE_VERSION),
        "--archive",
        "{chroot}/image.tar",
        *remote,
    )
    # The run happens outside the sandbox, so the builder is addressed through `{chroot}`. It
    # unpacks under the user's own `~/.cache`, as the run isn't cached.
    args = [f"{{chroot}}/{exe}", *runner_args]
    if request.interactive:
        args.append("--interactive")
    if request.tty:
        args.append("--tty")
    args.append("--")
    return Process(
        args,
        input_digest=archive.output_digest,
        immutable_input_digests=builder.immutable_input_digests,
        description=f"Running {request.address}",
    )


@rule
async def prepare_run_image_bundle(
    request: RunImageBundleProcessRequest, oci: OciSubsystem, union_membership: UnionMembership
) -> Process:
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.target.address, description_of_origin="run OCI image"),
        **implicitly(),
    )
    bundle_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())
    image = await resolve_image_bundle(**implicitly({bundle_request.request: FallibleImageBundleRequest}))
    if image.exit_code != 0 or image.dependency_failed or image.output is None:
        raise ValueError(f"Failed preparing OCI image {request.target.address}: {image.stderr}")
    run = runner_member(ContainerRunRequest, oci, union_membership)(
        image.output,
        request.target.address,
        interactive=request.interactive,
        tty=request.interactive or bool(wrapped_target.target.get(ImageRunTty).value),
    )
    return await container_run(**implicitly({run: ContainerRunRequest}))


@rule
async def run_oci_command_target(request: RunImageBundleCommand) -> RunRequest:
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.address, description_of_origin="package_oci_image"), **implicitly()
    )
    process = await prepare_run_image_bundle(
        RunImageBundleProcessRequest(wrapped_target.target), **implicitly()
    )

    return RunRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
        immutable_input_digests=process.immutable_input_digests,
    )


class OciRepl(ReplImplementation):
    name = "oci"
    supports_args = True


@rule
async def run_oci_command_repl(request: OciRepl) -> ReplRequest:
    process = await prepare_run_image_bundle(
        RunImageBundleProcessRequest(request.targets[0], interactive=True), **implicitly()
    )
    return ReplRequest(
        digest=process.input_digest,
        args=process.argv,
        extra_env=process.env,
        immutable_input_digests=process.immutable_input_digests,
    )


def rules():
    rules = [
        *collect_rules(),
        *RunImageBundleCommand.rules(),
        UnionRule(ReplImplementation, OciRepl),
        UnionRule(ContainerRunRequest, PodmanRunRequest),
    ]

    return rules
