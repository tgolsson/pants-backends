"""`pants lint` with clang-tidy over a target's compile steps for the host: one process per step
whose source is under `clang_tidy_paths`, in the sandbox that step compiles in, with its compile
command and environment. clang reads the host's C and C++ library headers from outside the
sandbox, as the compile does, so the toolchain fingerprint in that environment is what keys the
cache on them."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

from pants.backend.python.util_rules.pex import PexProcess, create_pex
from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.config_files import find_config_file
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import MergeDigests
from pants.engine.internals.graph import find_valid_field_sets, resolve_target
from pants.engine.intrinsics import execute_process, merge_digests
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import FieldSet, FieldSetsPerTargetRequest, Target, WrappedTargetRequest
from pants.util.logging import LogLevel
from pants.util.strutil import Simplifier

from pants_backend_cc.lint.clang_tidy.subsystem import ClangTidy
from pants_backend_cc.lint.clang_tidy.target_types import ClangTidyPathsField
from pants_backend_cc.util_rules.compile import (
    CompileInputsRequest,
    CompileStep,
    CompileStepsFieldSet,
    compile_inputs,
    compile_steps,
)
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform


@dataclass(frozen=True)
class ClangTidyFieldSet(FieldSet):
    required_fields = (ClangTidyPathsField,)

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


def header_filter(cwd: str, dirs: list[str]) -> str:
    """clang-tidy matches the filter against a header's path as the compiler opened it: relative
    to `cwd` when found through a `-I`, or under the absolute sandbox path of `cwd` when found
    next to the file including it. In the sandbox root that absolute path is the sandbox's own,
    which the process substitutes for `{chroot}`, so a system header's `/<dir>/` never matches."""
    prefixes = sorted({_relative(d, cwd) for d in dirs})
    alternatives = "|".join(re.escape(p) for p in prefixes)
    anchor = f"(^|/{re.escape(cwd)}/)" if cwd else "^(\\./|{chroot}/)?"
    return f"{anchor}({alternatives})/"


_DIAGNOSTIC_PATH = re.compile(r"^([^\s:]+)(?=:\d+:)", re.MULTILINE)


def _root_relative(output: str, cwd: str) -> str:
    """Diagnostics name files relative to `cwd` (`../src/a.h`), or by the sandbox path of `cwd`
    (`build/../src/a.cpp` once the sandbox is stripped)."""

    def fix(match: re.Match[str]) -> str:
        path = match.group(1)
        if not path.startswith(f"{cwd}/"):
            path = os.path.join(cwd, path)
        return os.path.normpath(path)

    return _DIAGNOSTIC_PATH.sub(fix, output)


_DIAGNOSTIC_START = re.compile(r"^(?:[^\s:]+:\d+:\d+: )?(?:error|warning): ", re.MULTILINE)


def _diagnostics(output: str) -> list[str]:
    """Each diagnostic with its source excerpt and notes, so the same finding from two compiles
    of a file, or from every file including a header, is reported once."""
    starts = [m.start() for m in _DIAGNOSTIC_START.finditer(output)]
    return [output[a:b] for a, b in zip(starts, [*starts[1:], len(output)])]


def _relative(path: str, cwd: str) -> str:
    return os.path.relpath(path, cwd or ".")


def _ancestors(paths: Iterable[str]) -> list[str]:
    dirs = set()
    for path in paths:
        parent = os.path.dirname(path)
        while parent:
            dirs.add(parent)
            parent = os.path.dirname(parent)
    return sorted(dirs)


async def _host_compile_steps(field_set: ClangTidyFieldSet, known: KnownPlatforms) -> list[CompileStep]:
    wrapped = await resolve_target(
        WrappedTargetRequest(field_set.address, description_of_origin="clang-tidy"), **implicitly()
    )
    per_target = await find_valid_field_sets(
        FieldSetsPerTargetRequest(CompileStepsFieldSet, [wrapped.target]), **implicitly()
    )
    providers = per_target.field_sets
    if len(providers) != 1:
        kinds = sorted(type(fs).__name__ for fs in providers)
        raise ValueError(
            f"{field_set.address}: `{ClangTidyPathsField.alias}` needs exactly one compile-step "
            f"provider for the target, found {kinds}"
        )
    steps = await compile_steps(
        **implicitly({providers[0]: CompileStepsFieldSet, known.host: TargetPlatform})
    )
    return list(steps)


@rule(desc="Lint with clang-tidy", level=LogLevel.DEBUG)
async def clang_tidy_lint(
    request: ClangTidyRequest.Batch, clang_tidy: ClangTidy, known: KnownPlatforms
) -> LintResult:
    (field_set,) = request.elements
    paths = field_set.paths.value or ()
    dirs = [os.path.normpath(os.path.join(field_set.address.spec_path, d)) for d in paths]
    steps = [
        s for s in await _host_compile_steps(field_set, known) if not s.generated and _under(s.source, dirs)
    ]
    if not steps:
        raise ValueError(
            f"{field_set.address}: no translation unit is under `{ClangTidyPathsField.alias}` {list(paths)}"
        )
    pex, config_files = await concurrently(
        create_pex(clang_tidy.to_pex_request()),
        find_config_file(clang_tidy.config_request(_ancestors(s.source for s in steps))),
    )
    inputs = await concurrently(compile_inputs(**implicitly({s.inputs: CompileInputsRequest})) for s in steps)
    input_digests = await concurrently(
        merge_digests(MergeDigests([i.digest, config_files.snapshot.digest])) for i in inputs
    )
    results = await concurrently(
        execute_process(
            **implicitly(
                PexProcess(
                    pex,
                    argv=(
                        "--quiet",
                        f"--header-filter={header_filter(step.cwd, dirs)}",
                        "--extra-arg=-Wno-unknown-warning-option",
                        *clang_tidy.args,
                        _relative(step.source, step.cwd),
                        "--",
                        # Without the compiler; `-c`, `-o` and depfile options are harmless, as
                        # clang-tidy writes no outputs.
                        *(a for a in step.argv[1:] if clang_tidy.keeps(a)),
                    ),
                    input_digest=input_digest,
                    working_directory=step.cwd or None,
                    extra_env=step.env,
                    description=f"Run clang-tidy on {step.source} ({_relative(step.object, step.cwd)})",
                    level=LogLevel.DEBUG,
                )
            )
        )
        for step, input_digest in zip(steps, input_digests)
    )

    failed = [(s, r) for s, r in zip(steps, results) if r.exit_code != 0]
    simplifier = Simplifier()
    diagnostics: dict[str, None] = {}
    stderr = ""
    for step, result in failed:
        found = _diagnostics(_root_relative(simplifier.simplify(result.stdout), step.cwd))
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
    return [*collect_rules(), *ClangTidyRequest.rules()]
