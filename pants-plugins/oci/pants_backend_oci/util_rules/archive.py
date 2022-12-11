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

from pants.core.util_rules.system_binaries import SEARCH_PATHS, TarBinary, TarBinaryRequest
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests, Snapshot
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.util.logging import LogLevel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateDeterministicTar:
    """A request to create a deterministic tar.
    All files in the input snapshot will be included in the resulting archive.
    """

    snapshot: Snapshot
    output_filename: str


@rule(desc="Creating an archive file", level=LogLevel.DEBUG)
async def create_archive(request: CreateDeterministicTar) -> Digest:
    # #16091 -- if an arg list is really long, archive utilities tend to get upset.
    # passing a list of filenames into the utilities fixes this.
    FILE_LIST_FILENAME = "__pants_archive_filelist__"
    file_list_file = FileContent(FILE_LIST_FILENAME, "\n".join(request.snapshot.files).encode("utf-8"))
    file_list_file_digest = await Get(Digest, CreateDigest([file_list_file]))
    files_digests = [file_list_file_digest, request.snapshot.digest]
    input_digests = []

    tar_binary = await Get(TarBinary, TarBinaryRequest())
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

    # `tar` expects to find a couple binaries like `gzip` and `xz` by looking on the PATH.
    env = {"PATH": os.pathsep.join(SEARCH_PATHS)}

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
