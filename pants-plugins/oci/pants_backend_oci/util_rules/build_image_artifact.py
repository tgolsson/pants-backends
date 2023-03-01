"""

"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from textwrap import dedent
from typing import ClassVar

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSet,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    Target,
    Targets,
    WrappedTarget,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import RuncTool, SkopeoTool, UmociTool
from pants_backend_oci.target_types import (
    ImageBase,
    ImageBuildCommand,
    ImageBuildOutputs,
    ImageDependencies,
)
from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.archive import CreateDeterministicTar
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundle,
    ImageBundleRequest,
)
from pants_backend_oci.util_rules.jq import JqBinary, JqBinaryRequest
from pants_backend_oci.util_rules.oci_sha import OciSha, OciShaRequest
from pants_backend_oci.util_rules.unpack import UnpackedImageBundleRequest


@dataclass(frozen=True)
class ImageArtifactBuildFieldSet(PackageFieldSet):
    required_fields = (ImageBase, ImageBuildCommand, ImageBuildOutputs)

    base: ImageBase

    commands: ImageBuildCommand
    outputs: ImageBuildOutputs

    dependencies: ImageDependencies


class ImageArtifactBuildRequest(FallibleImageBundleRequest):
    target: Target

    field_set_type: ClassVar[type[FieldSet]] = ImageArtifactBuildFieldSet


@rule(desc="Build artifact in container", level=LogLevel.DEBUG)
async def build_image_artifact(
    request: ImageArtifactBuildRequest,
    umoci: UmociTool,
    runc: RuncTool,
    platform: Platform,
    bash: BashBinary,
) -> Process:
    base = await Get(
        Addresses,
        UnparsedAddressInputs,
        request.target.base.to_unparsed_address_inputs(),
    )

    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(base[0], description_of_origin="package_oci_image"),
    )

    build_request = await Get(
        FallibleImageBundleRequestWrap, ImageBundleRequest(wrapped_target.target)
    )
    maybe_built_base = await Get(
        FallibleImageBundle, FallibleImageBundleRequest, build_request.request
    )

    if maybe_built_base.output is None:
        return dataclasses.replace(maybe_built_base, dependency_failed=True)

    umoci = await Get(DownloadedExternalTool, ExternalToolRequest, umoci.get_request(platform))
    base = maybe_built_base.output
    base_digest = base.digest

    if request.target.dependencies:
        root_dependencies = await Get(Targets, DependenciesRequest(request.target.dependencies))

        embedded_pkgs_per_target = await Get(
            FieldSetsPerTarget,
            FieldSetsPerTargetRequest(PackageFieldSet, root_dependencies),
        )

        # Package binary dependencies for build context.
        embedded_pkgs = await MultiGet(
            Get(BuiltPackage, PackageFieldSet, field_set)
            for field_set in embedded_pkgs_per_target.field_sets
        )

        embedded_pkgs_digest = [built_package.digest for built_package in embedded_pkgs]
        all_digests = embedded_pkgs_digest

        layer_digest = await Get(
            Digest,
            MergeDigests(all_digests),
        )

        snapshot = await Get(Snapshot, Digest, layer_digest)

        raw_layer_digest = await Get(
            Digest, CreateDeterministicTar(snapshot, "layers/image_bundle.tar")
        )

        command_digest = await Get(
            Digest,
            MergeDigests([raw_layer_digest, umoci.digest, base_digest]),
        )

        import datetime

        timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"
        config = [
            "--config.env",
            "BUILT_BY=pants.oci",
            "--config.entrypoint",
            f"/{embedded_pkgs[0].artifacts[0].relpath}",
            "--author=pants_backend_oci",
            f"--created={timestamp}",
            "--no-history",
        ]

        compile_result = await Get(
            FallibleProcessResult,
            FusedProcess(
                (
                    Process(
                        (
                            umoci.exe,
                            "raw",
                            "add-layer",
                            "--history.author=pants_backend_oci",
                            f"--history.created_by='Layer target: {request.target.address}'",
                            f"--history.comment='Layer target: {request.target.address}'",
                            f"--history.created={timestamp}",
                            "--image",
                            "build:build",
                            "layers/image_bundle.tar",
                        ),
                        input_digest=command_digest,
                        description=f"Package OCI Image Bundle: {request.target.address}",
                    ),
                    Process(
                        (
                            umoci.exe,
                            "config",
                            *config,
                            "--image",
                            "build:build",
                        ),
                        input_digest=command_digest,
                        description=f"Configure OCI Image: {request.target.address}",
                        output_directories=("build",),
                    ),
                ),
            ),
        )

        if compile_result.exit_code != 0:
            return FallibleImageBundle(
                None,
                compile_result.exit_code,
                stdout=compile_result.stdout.decode("utf-8"),
                stderr=compile_result.stderr.decode("utf-8"),
            )

        compilation_digest = compile_result.output_digest
        output_digest = compilation_digest

    else:
        output_digest = base_digest

    download_runc_tool = Get(
        DownloadedExternalTool, ExternalToolRequest, runc.get_request(platform)
    )
    wrapped_target = await Get(
        WrappedTarget,
        WrappedTargetRequest(request.target.address, description_of_origin="package_oci_image"),
    )

    tool, rundir, jq = await MultiGet(
        download_runc_tool,
        Get(Digest, CreateDigest([Directory("runspace")])),
        Get(JqBinary, JqBinaryRequest()),
    )

    packed_image_process = await Get(Process, UnpackedImageBundleRequest(output_digest))
    name = str(request.target.address).replace("/", "_").replace(":", "_").replace("#", "_")
    script = dedent(
        f"""
        ROOT=`pwd`
        for arg in "$@"; do
            echo "$arg" | {jq.path} -R --argjson config "$(cat $ROOT/unpacked_image/config.json)" '
                    .  as $arg  | $config | .process.args += [$arg]
                ' > "$ROOT/unpacked_image/config.json"
        done
        `pwd`/{tool.exe} --root runspace run -b unpacked_image pants.runc.{name}
        exit 1
    """
    )
    script_digest = await Get(Digest, CreateDigest([FileContent("run.sh", script.encode("utf-8"))]))
    input_digest = await Get(Digest, MergeDigests((rundir, tool.digest, script_digest)))

    return await Get(
        Process,
        FusedProcess(
            (
                packed_image_process,
                Process(
                    (bash.path, "{chroot}/run.sh", "$*"),
                    description=f"Running {request.target}",
                    input_digest=input_digest,
                    output_directories=tuple(f"runspace/{v}" for v in request.target.outputs.value),
                ),
            )
        ),
    )


def rules():
    return [
        *collect_rules(),
    ]
