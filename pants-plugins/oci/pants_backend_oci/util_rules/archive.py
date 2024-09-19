# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).
#
# This file is derived from the following upstream file:
# https://github.com/pantsbuild/pants/blob/5a15cb1286300f3cb51a8b28671456962a2ea065/src/python/pants/core/util_rules/archive.py.
#
# Modifications are made to strip metadata.

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from pants.core.util_rules.system_binaries import (
    BinaryPathRequest,
    BinaryPaths,
    BinaryPathTest,
    SystemBinariesSubsystem,
    TarBinary,
)
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_oci.subsystem import OciSubsystem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TarEnvironment:
    env: FrozenDict[str, str]


@dataclass(frozen=True)
class GTarBinary(TarBinary):
    pass


@rule
def tar_environment(
    system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
) -> TarEnvironment:
    env = {"PATH": os.pathsep.join(system_binaries_subsystem.system_binary_paths), "GZIP": "-n"}
    return TarEnvironment(env=FrozenDict(env))


@rule(desc="Finding the `tar` binary", level=LogLevel.DEBUG)
async def find_gtar(
    platform: Platform, system_binaries: SystemBinariesSubsystem.EnvironmentAware
) -> GTarBinary:
    request = BinaryPathRequest(
        binary_name="gtar",
        search_path=system_binaries.system_binary_paths,
        test=BinaryPathTest(args=["--version"]),
    )
    paths = await Get(BinaryPaths, BinaryPathRequest, request)
    first_path = paths.first_path_or_raise(request, rationale="download the tools Pants needs to run")
    return GTarBinary(first_path.path, first_path.fingerprint, platform)


@dataclass(frozen=True)
class CreateDeterministicTar:
    """A request to create a deterministic tar.
    All files in the input snapshot will be included in the resulting archive.
    """

    snapshot: Snapshot
    output_filename: str


@dataclass(frozen=True)
class CreateDeterministicDirectoryTar:
    """A request to create a deterministic tar.
    All files in the input snapshot will be included in the resulting archive.
    """

    directory: str
    output_filename: str

    gzip: bool = True
    exclude_patterns: tuple[str, ...] = tuple()


@rule
async def tar_directory_process(
    request: CreateDeterministicDirectoryTar, env: TarEnvironment, platform: Platform
) -> Process:
    if platform in (Platform.macos_arm64, Platform.macos_x86_64):
        tar_binary = await Get(GTarBinary)

    else:
        tar_binary = await Get(TarBinary)

    argv = [
        tar_binary.path,
        "--sort=name",
        "--mtime=1970-01-01 00:00Z",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
    ]

    if request.gzip:
        argv.append("--gzip")

    for pattern in request.exclude_patterns:
        argv.extend(["--exclude", pattern])

    argv.extend(["-cf", request.output_filename, "-C", request.directory, "."])

    # `tar` expects to find a couple binaries like `gzip` and `xz` by looking on the PATH.
    env = {**env.env}

    # `tar` requires that the output filename's parent directory exists,so if the caller
    # wants the output in a directory we explicitly create it here.
    # We have to guard this path as the Rust code will crash if we give it empty paths.
    output_dir = os.path.dirname(request.output_filename)
    input_digest = None
    if output_dir != "":
        input_digest = await Get(Digest, CreateDigest([Directory(output_dir)]))

    return Process(
        argv=argv,
        env=env,
        input_digest=input_digest,
        description=f"Create {request.output_filename}",
        level=LogLevel.DEBUG,
        output_files=(request.output_filename,),
    )


@rule(desc="Creating an archive file", level=LogLevel.DEBUG)
async def create_archive(
    request: CreateDeterministicTar, env: TarEnvironment, oci_subsystem: OciSubsystem, platform: Platform
) -> Digest:
    if platform in (Platform.macos_arm64, Platform.macos_x86_64):
        tar_binary = await Get(GTarBinary)

    else:
        tar_binary = await Get(TarBinary)

    # #16091 -- if an arg list is really long, archive utilities tend to get upset.
    # passing a list of filenames into the utilities fixes this.
    FILE_LIST_FILENAME = "__pants_archive_filelist__"
    file_list_file = FileContent(FILE_LIST_FILENAME, "\n".join(request.snapshot.files).encode("utf-8"))
    file_list_file_digest = await Get(Digest, CreateDigest([file_list_file]))
    files_digests = [file_list_file_digest, request.snapshot.digest]
    input_digests = []

    argv = list(
        tar_binary.create_archive_argv(
            request.output_filename,
            "tar",
            input_file_list_filename=FILE_LIST_FILENAME,
        )
    )

    argv[3:3] = [
        "--sort=name",
        "--mtime=1970-01-01 00:00Z",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
    ]

    if oci_subsystem.unsafe_tar_ignore_file_changed:
        argv[8:8] = ["--warning=no-file-changed"]

    # `tar` expects to find a couple binaries like `gzip` and `xz` by looking on the PATH.
    env = {**env.env}

    # `tar` requires that the output filename's parent directory exists,so if the caller
    # wants the output in a directory we explicitly create it here.
    # We have to guard this path as the Rust code will crash if we give it empty paths.
    output_dir = os.path.dirname(request.output_filename)
    if output_dir != "":
        output_dir_digest = await Get(Digest, CreateDigest([Directory(output_dir)]))
        input_digests.append(output_dir_digest)

    input_digest = await Get(Digest, MergeDigests([*files_digests, *input_digests]))

    result = await Get(
        ProcessResult,
        Process(
            argv=argv,
            env=env,
            input_digest=input_digest,
            description=f"Create {request.output_filename}",
            level=LogLevel.DEBUG,
            output_files=(request.output_filename,),
        ),
    )
    return result.output_digest


def rules():
    return collect_rules()
