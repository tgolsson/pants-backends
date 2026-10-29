"""`pants lint` with clang-tidy over a `cmake_project` configured for the host: one process per
compile edge whose source is under `clang_tidy_paths`, in the sandbox that edge compiles in
(`edge_inputs`), with its compile command and its toolchain's environment. clang reads the host's
C and C++ library headers from outside the sandbox, as the compile does, so the toolchain
fingerprint in that environment is what keys the cache on them."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass

from pants.backend.python.util_rules.pex import PexProcess, create_pex
from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.config_files import find_config_file
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import MergeDigests
from pants.engine.intrinsics import execute_process, merge_digests
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import FieldSet, StringSequenceField, Target
from pants.util.logging import LogLevel
from pants.util.strutil import Simplifier, softwrap

from pants_backend_cmake.lint.clang_tidy.subsystem import ClangTidy
from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.target_types import CMakeProjectSourcesField, CMakeProjectTarget
from pants_backend_cmake.util_rules.canonicalize import rebase
from pants_backend_cmake.util_rules.configure import (
    ConfiguredProject,
    ConfigureRequest,
    configure_project,
)
from pants_backend_cmake.util_rules.replay import EdgeRequest, edge_inputs
from pants_backend_cmake.util_rules.scan import SCAN_OVERRIDES, is_compile_edge, workspace_path
from pants_backend_platforms.util_rules.platforms import KnownPlatforms


class ClangTidyPathsField(StringSequenceField):
    alias = "clang_tidy_paths"
    default = ()
    help = softwrap(
        """
        Directories, relative to this BUILD file, whose translation units `[clang-tidy]` lints
        and whose headers it reports diagnostics in (its `--header-filter`); none means the
        project is not linted. A translation unit compiled by several CMake targets is linted
        once per compile, with each one's flags.
        """
    )


@dataclass(frozen=True)
class ClangTidyFieldSet(FieldSet):
    required_fields = (CMakeProjectSourcesField, ClangTidyPathsField)

    paths: ClangTidyPathsField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return not tgt[ClangTidyPathsField].value


class ClangTidyRequest(LintTargetsRequest):
    field_set_type = ClangTidyFieldSet
    tool_subsystem = ClangTidy  # type: ignore[assignment]
    partitioner_type = PartitionerType.DEFAULT_ONE_PARTITION_PER_INPUT


def _under(path: str, dirs: list[str]) -> bool:
    return any(d == "." or path == d or path.startswith(f"{d}/") for d in dirs)


def linted_units(project: ConfiguredProject, dirs: list[str]) -> list[tuple[NinjaEdge, str]]:
    """The compile edges whose source is a workspace file under `dirs` (build-root-relative),
    with that source."""
    units = []
    for edge in project.graph.edges:
        if not is_compile_edge(edge) or not edge.inputs:
            continue
        source = workspace_path(edge.inputs[0])
        if source is not None and _under(source, dirs):
            units.append((edge, source))
    return units


def header_filter(project: ConfiguredProject, dirs: list[str]) -> str:
    """clang-tidy matches the filter against a header's path as the compiler opened it: relative
    to the build directory when found through a (rebased) `-I`, or under the absolute sandbox
    path of the build directory when found next to the file including it."""
    prefixes = sorted({os.path.relpath(d, project.build_dir) for d in dirs})
    alternatives = "|".join(re.escape(p) for p in prefixes)
    return f"(^|/{re.escape(project.build_dir)}/)({alternatives})/"


def compile_args(project: ConfiguredProject, edge: NinjaEdge) -> list[str]:
    """The edge's compile command without the compiler, relative to the build directory. Its
    `-c`, `-o` and depfile options are harmless: clang-tidy writes no outputs."""
    command = project.graph.command(edge, overrides=SCAN_OVERRIDES)
    return shlex.split(rebase(command, project.relative_root))[1:]


_DIAGNOSTIC_PATH = re.compile(r"^([^\s:]+)(?=:\d+:)", re.MULTILINE)


def _root_relative(output: str, build_dir: str) -> str:
    """Diagnostics name files relative to the build directory (`../src/a.h`), or by the sandbox
    path of the build directory (`build/../src/a.cpp` once the sandbox is stripped)."""

    def fix(match: re.Match[str]) -> str:
        path = match.group(1)
        if not path.startswith(f"{build_dir}/"):
            path = os.path.join(build_dir, path)
        return os.path.normpath(path)

    return _DIAGNOSTIC_PATH.sub(fix, output)


_DIAGNOSTIC_START = re.compile(r"^(?:[^\s:]+:\d+:\d+: )?(?:error|warning): ", re.MULTILINE)


def _diagnostics(output: str) -> list[str]:
    """Each diagnostic with its source excerpt and notes, so the same finding from two compiles
    of a file, or from every file including a header, is reported once."""
    starts = [m.start() for m in _DIAGNOSTIC_START.finditer(output)]
    return [output[a:b] for a, b in zip(starts, [*starts[1:], len(output)])]


def _ancestors(paths: Iterable[str]) -> list[str]:
    dirs = set()
    for path in paths:
        parent = os.path.dirname(path)
        while parent:
            dirs.add(parent)
            parent = os.path.dirname(parent)
    return sorted(dirs)


@rule(desc="Lint with clang-tidy", level=LogLevel.DEBUG)
async def clang_tidy_lint(
    request: ClangTidyRequest.Batch, clang_tidy: ClangTidy, known: KnownPlatforms
) -> LintResult:
    (field_set,) = request.elements
    project = await configure_project(ConfigureRequest(field_set.address, known.host), **implicitly())
    dirs = [os.path.normpath(os.path.join(project.project_dir, d)) for d in field_set.paths.value or ()]
    units = linted_units(project, dirs)
    if not units:
        raise ValueError(
            f"{field_set.address}: no translation unit is under `{ClangTidyPathsField.alias}` "
            f"{list(field_set.paths.value or ())}"
        )
    pex, config_files = await concurrently(
        create_pex(clang_tidy.to_pex_request()),
        find_config_file(clang_tidy.config_request(_ancestors(source for _, source in units))),
    )
    inputs = await concurrently(edge_inputs(EdgeRequest(project, edge), **implicitly()) for edge, _ in units)
    input_digests = await concurrently(
        merge_digests(MergeDigests([i.digest, config_files.snapshot.digest])) for i in inputs
    )
    filter_arg = f"--header-filter={header_filter(project, dirs)}"
    results = await concurrently(
        execute_process(
            **implicitly(
                PexProcess(
                    pex,
                    argv=(
                        "--quiet",
                        filter_arg,
                        "--extra-arg=-Wno-unknown-warning-option",
                        *clang_tidy.args,
                        os.path.relpath(source, project.build_dir),
                        "--",
                        *(a for a in compile_args(project, edge) if clang_tidy.keeps(a)),
                    ),
                    input_digest=input_digest,
                    working_directory=project.build_dir,
                    extra_env=project.toolchain.env,
                    description=f"Run clang-tidy on {source} ({edge.outputs[0]})",
                    level=LogLevel.DEBUG,
                )
            )
        )
        for (edge, source), input_digest in zip(units, input_digests)
    )

    failed = [r for r in results if r.exit_code != 0]
    simplifier = Simplifier()
    diagnostics: dict[str, None] = {}
    stderr = ""
    for result in failed:
        found = _diagnostics(_root_relative(simplifier.simplify(result.stdout), project.build_dir))
        diagnostics.update(dict.fromkeys(found))
        # Without a diagnostic on stdout, stderr is all that says why the compile failed.
        if not found:
            stderr += simplifier.simplify(result.stderr)
    stderr += f"clang-tidy: {len(failed)} of {len(results)} compiles failed\n"
    return LintResult(
        exit_code=1 if failed else 0,
        stdout="".join(diagnostics),
        stderr=stderr,
        linter_name=request.tool_name,
        partition_description=str(field_set.address),
    )


def rules():
    return [
        *collect_rules(),
        *ClangTidyRequest.rules(),
        CMakeProjectTarget.register_plugin_field(ClangTidyPathsField),
    ]
