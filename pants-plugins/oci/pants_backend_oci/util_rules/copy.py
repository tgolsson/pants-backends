from __future__ import annotations

import os
from dataclasses import dataclass
from textwrap import dedent

from pants.core.util_rules.system_binaries import BashBinary, CpBinary, MkdirBinary
from pants.engine.fs import CreateDigest, Directory, FileContent
from pants.engine.intrinsics import create_digest
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule

from pants_backend_oci.tools.process import FusedProcess
from pants_backend_oci.util_rules.archive import (
    CreateDeterministicDirectoryTar,
    tar_directory_process,
)
from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.unpack import UnpackedImageBundleRequest, make_unpack_process


@dataclass(frozen=True)
class CopyFromRequest:
    tar_name: str
    bundle: ImageBundle
    output_files: tuple[str]
    output_directories: tuple[str]

    exclude_patterns: tuple[str, ...] = tuple()


@rule
async def copy_from_container(
    request: CopyFromRequest,
    platform: Platform,
    bash: BashBinary,
    cp: CpBinary,
    mkdir: MkdirBinary,
) -> ProcessResult:
    copy_list = [
        dedent(f"""
        {mkdir.path} -p out/{os.path.dirname(path)}
        {cp.path} -r unpacked_image/rootfs/{path} out/{path}
        """)
        for path in request.output_directories
    ]

    copy_list.extend(
        dedent(f"""
        {mkdir.path} -p out/{os.path.dirname(path)}
        {cp.path} unpacked_image/rootfs/{path} out/
        """)
        for path in request.output_files
    )

    unpack, tar, workspace_digest = await concurrently(
        make_unpack_process(UnpackedImageBundleRequest(request.bundle.digest), **implicitly()),
        tar_directory_process(
            CreateDeterministicDirectoryTar(
                "out",
                request.tar_name,
                gzip=request.tar_name.endswith(".gz"),
                exclude_patterns=request.exclude_patterns,
            ),
            **implicitly(),
        ),
        create_digest(
            CreateDigest(
                [
                    FileContent("copy.sh", ";\n".join(copy_list).encode("utf-8")),
                    Directory("out"),
                    Directory("runspace"),
                ]
            )
        ),
    )

    steps = [
        unpack,
        Process(
            (bash.path, "{chroot}/copy.sh"),
            description="Collecting files",
            input_digest=workspace_digest,
        ),
        tar,
    ]

    res = await fallible_to_exec_result_or_raise(**implicitly(FusedProcess(tuple(steps))))

    return res


def rules():
    return collect_rules()
