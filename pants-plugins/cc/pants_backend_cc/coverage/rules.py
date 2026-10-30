"""`pants test --use-coverage` reports for `cc_test`. gcov-tool sums the `.gcda` files the tests
wrote, two runs at a time, so that one gcovr run reads every object's total next to its notes into
a JSON tracefile; a last gcovr run writes the reports `[gcovr].report` names under
`dist/coverage/cc/`.

Tests whose notes or compile inputs differ at a path, such as two parametrizations of a project
that share a build directory, cannot share that tree: each group of tests that agree gets a
tracefile of its own, and the last run adds them up, given only the sources they name.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import PurePath

from pants.backend.python.subsystems.python_tool_base import get_lockfile_interpreter_constraints
from pants.backend.python.util_rules.pex import Pex, PexProcess, create_pex
from pants.core.goals.test import (
    ConsoleCoverageReport,
    CoverageDataCollection,
    CoverageReport,
    CoverageReports,
    FilesystemCoverageReport,
)
from pants.core.util_rules.distdir import DistDir
from pants.engine.fs import (
    AddPrefix,
    CreateDigest,
    Digest,
    DigestSubset,
    Directory,
    FileEntry,
    MergeDigests,
    PathGlobs,
    RemovePrefix,
)
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_subset_to_digest,
    digest_to_snapshot,
    execute_process,
    get_digest_contents,
    get_digest_entries,
    merge_digests,
    remove_prefix,
)
from pants.engine.process import (
    FallibleProcessResult,
    Process,
    ProcessExecutionFailure,
    fallible_to_exec_result_or_raise,
)
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.unions import UnionRule
from pants.option.global_options import KeepSandboxes
from pants.util.logging import LogLevel

from pants_backend_cc.coverage.data import CcCoverageDataCollection
from pants_backend_cc.coverage.gcov import GCOV, GcovTools, find_gcov_tools
from pants_backend_cc.coverage.subsystem import Gcovr, GcovrReportType

TRACEFILE = "coverage.json"
TRACEFILES = "__tracefiles"
REPORTS = "__reports"

# What gcovr writes for each report type under `REPORTS`; the console report is printed.
_REPORT_ARGS = {
    GcovrReportType.CONSOLE: ("--txt", "coverage.txt"),
    GcovrReportType.HTML: ("--html-details", "html/index.html"),
    GcovrReportType.JSON: ("--json", "coverage.json"),
    GcovrReportType.JSON_SUMMARY: ("--json-summary", "coverage-summary.json"),
    GcovrReportType.COBERTURA: ("--cobertura", "coverage.xml"),
    GcovrReportType.LCOV: ("--lcov", "coverage.lcov"),
    GcovrReportType.MARKDOWN: ("--markdown", "coverage.md"),
}

# gcovr's exit status is a set of bits; these say only that a `--fail-under-*` threshold (line,
# branch, condition or decision, function) was missed, after every report was written.
_BELOW_THRESHOLD = 2 | 4 | 8 | 16


def below_threshold(exit_code: int) -> bool:
    return exit_code != 0 and exit_code & ~_BELOW_THRESHOLD == 0


def agreeing_groups(files: Sequence[Mapping[str, object]]) -> list[list[int]]:
    """The tests, by index, in groups whose files agree at every path they share; each test joins
    the first group it agrees with. `files` maps each test's paths to their contents."""
    groups: list[tuple[list[int], dict[str, object]]] = []
    for index, entries in enumerate(files):
        for members, merged in groups:
            if all(merged.get(path, content) == content for path, content in entries.items()):
                members.append(index)
                merged.update(entries)
                break
        else:
            groups.append(([index], dict(entries)))
    return [members for members, _ in groups]


async def _gcovr_pex(gcovr: Gcovr) -> Pex:
    # Left to itself, Pants 2.31 builds a tool from its lockfile for `[python]`'s interpreter
    # constraints, which may admit a Python older than gcovr supports.
    constraints = await get_lockfile_interpreter_constraints(gcovr)
    return await create_pex(gcovr.to_pex_request(interpreter_constraints=constraints))


def _selection(gcovr: Gcovr) -> tuple[str, ...]:
    return (
        *(f"--filter={f}" for f in gcovr.filter),
        *(f"--exclude={e}" for e in gcovr.exclude),
    )


async def _run_gcovr(process: PexProcess, keep_sandboxes: KeepSandboxes) -> FallibleProcessResult:
    result = await execute_process(**implicitly({process: PexProcess}))
    if result.exit_code != 0 and not below_threshold(result.exit_code):
        raise ProcessExecutionFailure(
            result.exit_code,
            result.stdout,
            result.stderr,
            process.description,
            keep_sandboxes=keep_sandboxes,
        )
    return result


async def _sum_two(tools: GcovTools, pair: Sequence[Digest]) -> Digest:
    if len(pair) == 1:
        return pair[0]
    # gcov-tool takes two directories, which must exist even when a run wrote nothing.
    directories, first, second = await concurrently(
        create_digest(CreateDigest([Directory("a"), Directory("b")])),
        add_prefix(AddPrefix(pair[0], "a")),
        add_prefix(AddPrefix(pair[1], "b")),
    )
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(tools.gcov_tool, "merge", "-o", "sum", "a", "b"),
                input_digest=await merge_digests(MergeDigests([directories, first, second])),
                output_directories=("sum",),
                env={"__PANTS_GCOV": tools.fingerprint},
                description="Sum gcov data",
                level=LogLevel.DEBUG,
            )
        )
    )
    return await remove_prefix(RemovePrefix(result.output_digest, "sum"))


async def _sum_gcda(tools: GcovTools, runs: list[Digest]) -> Digest:
    """The `.gcda` files of `runs` summed, pairwise in a balanced tree: a changed run reruns only
    the sums above it."""
    while len(runs) > 1:
        runs = list(await concurrently(_sum_two(tools, runs[i : i + 2]) for i in range(0, len(runs), 2)))
    return runs[0]


async def _tracefile(
    gcovr: Gcovr,
    pex: Pex,
    tools: GcovTools,
    tree: Mapping[str, FileEntry],
    runs: list[Digest],
    keep_sandboxes: KeepSandboxes,
) -> Digest:
    """gcovr's JSON at `TRACEFILE`, over the notes and sources in `tree` and the sum of the
    `.gcda` files in `runs`."""
    inputs, summed = await concurrently(
        create_digest(CreateDigest(tree.values())),
        _sum_gcda(tools, runs),
    )
    result = await _run_gcovr(
        PexProcess(
            pex,
            argv=(
                "--root=.",
                f"--gcov-executable={{chroot}}/{GCOV}",
                "-j{pants_concurrency}",
                f"--json={TRACEFILE}",
                *_selection(gcovr),
                *gcovr.args,
            ),
            input_digest=await merge_digests(MergeDigests([inputs, summed, tools.wrapper])),
            extra_env={"__PANTS_GCOV": tools.fingerprint},
            output_files=(TRACEFILE,),
            concurrency_available=max(1, sum(p.endswith(".gcno") for p in tree)),
            description=f"Read the gcov data of {len(runs)} C/C++ tests",
            level=LogLevel.DEBUG,
        ),
        keep_sandboxes,
    )
    return result.output_digest


@rule(desc="Generate C/C++ coverage reports", level=LogLevel.DEBUG)
async def generate_cc_coverage_reports(
    collection: CcCoverageDataCollection,
    gcovr: Gcovr,
    distdir: DistDir,
    keep_sandboxes: KeepSandboxes,
) -> CoverageReports:
    data = sorted(collection, key=lambda d: d.address)
    pex, tools, all_entries, gcda = await concurrently(
        _gcovr_pex(gcovr),
        find_gcov_tools(**implicitly()),
        concurrently(get_digest_entries(d.digest) for d in data),
        concurrently(digest_subset_to_digest(DigestSubset(d.digest, PathGlobs(["**/*.gcda"]))) for d in data),
    )
    files = [
        {e.path: e for e in entries if isinstance(e, FileEntry) and not e.path.endswith(".gcda")}
        for entries in all_entries
    ]
    groups = agreeing_groups(files)
    trees = [{path: e for i in members for path, e in files[i].items()} for members in groups]
    tracefiles = await concurrently(
        _tracefile(gcovr, pex, tools, tree, [gcda[i] for i in members], keep_sandboxes)
        for tree, members in zip(trees, groups)
    )

    contents = await concurrently(get_digest_contents(t) for t in tracefiles)
    named = {f["file"] for (content,) in contents for f in json.loads(content.content)["files"]}
    # Where the trees still differ at a source they name, the report shows the first one's.
    sources: dict[str, FileEntry] = {}
    for tree in trees:
        for path in named & tree.keys():
            sources.setdefault(path, tree[path])
    placed, source_digest, report_dirs = await concurrently(
        concurrently(add_prefix(AddPrefix(t, f"{TRACEFILES}/{i}")) for i, t in enumerate(tracefiles)),
        create_digest(CreateDigest(sources.values())),
        create_digest(CreateDigest([Directory(f"{REPORTS}/html")])),
    )

    reports = gcovr.report
    fail_under = () if gcovr.fail_under_line is None else (f"--fail-under-line={gcovr.fail_under_line}",)
    result = await _run_gcovr(
        PexProcess(
            pex,
            argv=(
                "--root=.",
                f"--add-tracefile={TRACEFILES}/*/{TRACEFILE}",
                *_selection(gcovr),
                *(f"{_REPORT_ARGS[r][0]}={REPORTS}/{_REPORT_ARGS[r][1]}" for r in dict.fromkeys(reports)),
                *fail_under,
                *gcovr.args,
            ),
            input_digest=await merge_digests(MergeDigests([*placed, source_digest, report_dirs])),
            output_directories=(REPORTS,),
            description=f"Generate C/C++ coverage reports for {len(data)} tests",
            level=LogLevel.DEBUG,
        ),
        keep_sandboxes,
    )
    insufficient = below_threshold(result.exit_code)
    written = await remove_prefix(RemovePrefix(result.output_digest, REPORTS))
    output_dir = PurePath(distdir.relpath, "coverage", "cc")
    built: list[CoverageReport] = []
    for report in dict.fromkeys(reports):
        path = _REPORT_ARGS[report][1]
        glob = f"{os.path.dirname(path)}/**" if os.path.dirname(path) else path
        subset = await digest_subset_to_digest(DigestSubset(written, PathGlobs([glob])))
        if report == GcovrReportType.CONSOLE:
            (console,) = await get_digest_contents(subset)
            built.append(ConsoleCoverageReport(insufficient, console.content.decode()))
            continue
        built.append(
            FilesystemCoverageReport(
                coverage_insufficient=insufficient,
                result_snapshot=await digest_to_snapshot(subset),
                directory_to_materialize_to=output_dir,
                report_file=output_dir / path,
                report_type=report.value,
            )
        )
    return CoverageReports(tuple(built))


def rules():
    return [*collect_rules(), UnionRule(CoverageDataCollection, CcCoverageDataCollection)]
