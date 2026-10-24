""" """

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.repl import ReplImplementation, ReplRequest
from pants.core.goals.run import RunFieldSet, RunInSandboxBehavior, RunRequest
from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.engine.fs import MergeDigests
from pants.engine.internals.graph import resolve_target
from pants.engine.intrinsics import create_digest, merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import Target, WrappedTargetRequest
from pants.engine.unions import UnionRule
from pants.option.global_options import NamedCachesDirOption

from pants_backend_oci.goals.package import OciArchiveRequest, package_oci_archive
from pants_backend_oci.target_types import ImageRepository, ImageRunTty
from pants_backend_oci.util_rules import podman_runner
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundleRequest,
    ImageBundleRequest,
    ibr_to_fibr,
    resolve_image_bundle,
)
from pants_backend_oci.util_rules.run import PODMAN_CACHE, PODMAN_STORE_VERSION, PodmanBinary
from pants_backend_oci.util_rules.sandboxed import sandboxed_modules


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


@rule
async def prepare_run_image_bundle(
    request: RunImageBundleProcessRequest,
    podman: PodmanBinary,
    python: PythonBuildStandaloneBinary,
    caches: NamedCachesDirOption,
    platform: Platform,
) -> Process:
    if platform not in (Platform.linux_x86_64, Platform.linux_arm64):
        raise ValueError("OCI run/repl requires local Linux podman; podman machine is not supported yet.")
    wrapped_target = await resolve_target(
        WrappedTargetRequest(request.target.address, description_of_origin="run OCI image"),
        **implicitly(),
    )
    bundle_request = await ibr_to_fibr(ImageBundleRequest(wrapped_target.target), **implicitly())
    image = await resolve_image_bundle(**implicitly({bundle_request.request: FallibleImageBundleRequest}))
    if image.exit_code != 0 or image.dependency_failed or image.output is None:
        raise ValueError(f"Failed preparing OCI image {request.target.address}: {image.stderr}")

    # Only the image's own blobs go to podman, in an OCI archive it reads directly.
    archive_process = await package_oci_archive(
        OciArchiveRequest(
            image.output.digest, "image.tar", None, f"Archive {request.target.address} for podman"
        ),
        **implicitly(),
    )
    archive = await fallible_to_exec_result_or_raise(**implicitly({archive_process: Process}))
    launcher = await create_digest(sandboxed_modules(podman_runner))
    digest = await merge_digests(MergeDigests([archive.output_digest, launcher]))
    args = [
        python.path,
        "{chroot}/pants_backend_oci/util_rules/podman_runner.py",
        "--podman",
        podman.path,
        "--cache",
        str(caches.val / PODMAN_CACHE / PODMAN_STORE_VERSION),
        "--archive",
        "{chroot}/image.tar",
    ]
    if request.interactive:
        args.append("--interactive")
    if request.interactive or wrapped_target.target.get(ImageRunTty).value:
        args.append("--tty")
    args.append("--")
    return Process(args, input_digest=digest, description=f"Running {request.target.address}")


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
    ]

    return rules
