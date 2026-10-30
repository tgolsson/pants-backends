"""`pants lint` measuring compile cost: each host compile step of a target whose source is under
`compile_cost_paths` runs again in its own sandbox, with its command, its environment and gcc's
`-ftime-report`, and the CPU time of its phases goes into a report. User CPU time is what counts:
wall time and system time both grow with the parallel compiles' contention for the machine."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.addresses import Address
from pants.engine.fs import CreateDigest, Digest, Directory, FileContent, MergeDigests
from pants.engine.intrinsics import create_digest, execute_process, merge_digests
from pants.engine.process import Process, ProcessCacheScope
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import FieldSet, Target
from pants.util.logging import LogLevel

from pants_backend_cc.lint.compile_cost.subsystem import CompileCost
from pants_backend_cc.lint.compile_cost.target_types import (
    CompileCostExemptPathsField,
    CompileCostPathsField,
)
from pants_backend_cc.lint.steps import build_root_dirs, steps_under, under
from pants_backend_cc.util_rules.compile import CompileInputsRequest, CompileStep, compile_inputs
from pants_backend_platforms.util_rules.platforms import KnownPlatforms

REPORT = "compile-cost.md"

# The number of the measurement, so the engine runs a second one rather than reuse the first.
SAMPLE_ENV = "PANTS_COMPILE_COST_SAMPLE"


@dataclass(frozen=True)
class CompileCostFieldSet(FieldSet):
    required_fields = (CompileCostPathsField,)

    paths: CompileCostPathsField
    exempt_paths: CompileCostExemptPathsField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return not tgt[CompileCostPathsField].value


class CompileCostRequest(LintTargetsRequest):
    field_set_type = CompileCostFieldSet
    tool_subsystem = CompileCost  # type: ignore[assignment]
    partitioner_type = PartitionerType.DEFAULT_ONE_PARTITION_PER_INPUT


@dataclass(frozen=True)
class Phases:
    """Seconds of user CPU time, and the frontend's system time, which is reported but not gated."""

    frontend: float
    frontend_sys: float
    backend: float
    total: float


# ` phase parsing   :   0.06 ( 10%)   0.07 ( 58%)   0.13 ( 18%)    14M ( 42%)`: user, system,
# wall, memory. gcc leaves out a phase that took no time.
_PHASE = re.compile(r"^\s*phase (?P<name>.+?)\s*:\s*(?P<usr>\d+\.\d+)\s*\([^)]*\)\s*(?P<sys>\d+\.\d+)", re.M)
_TOTAL = re.compile(r"^\s*TOTAL\s*:\s*(?P<usr>\d+\.\d+)\s+(?P<sys>\d+\.\d+)", re.M)
FRONTEND_PHASES = frozenset({"parsing", "lang. deferred"})


def parse_time_report(stderr: str) -> Phases | None:
    """The phases of gcc's `-ftime-report`; None when `stderr` has none, as from clang."""
    phases = {m["name"]: (float(m["usr"]), float(m["sys"])) for m in _PHASE.finditer(stderr)}
    total = _TOTAL.search(stderr)
    if not phases or total is None:
        return None
    frontend = [t for name, t in phases.items() if name in FRONTEND_PHASES]
    frontend_usr = sum(usr for usr, _ in frontend)
    return Phases(
        frontend=frontend_usr,
        frontend_sys=sum(sys for _, sys in frontend),
        backend=sum(usr for usr, _ in phases.values()) - frontend_usr,
        total=float(total["usr"]),
    )


def lower(first: Phases, second: Phases) -> Phases:
    """The sample with the lower frontend; the first on a tie."""
    return second if second.frontend < first.frontend else first


@dataclass(frozen=True)
class Row:
    tu: str
    phases: Phases
    gated: bool

    def over(self, budget: float | None) -> bool:
        return self.gated and budget is not None and self.phases.frontend > budget


def verdict(rows: Sequence[Row], budget: float | None) -> str:
    gated = sum(r.gated for r in rows)
    if budget is None:
        return f"{gated} gated TUs, no frontend budget set"
    over = sum(r.over(budget) for r in rows)
    return f"{over} of {gated} gated TUs over the {budget:g} s frontend budget"


def summary(rows: Sequence[Row], budget: float | None) -> str:
    """The verdict, then each TU over budget, slowest frontend first."""
    over = sorted((r for r in rows if r.over(budget)), key=lambda r: r.phases.frontend, reverse=True)
    lines = [f"compile-cost: {verdict(rows, budget)}"]
    lines.extend(f"  {r.tu}: {r.phases.frontend:.3f} s" for r in over)
    return "\n".join(lines) + "\n"


def render(rows: Sequence[Row], budget: float | None, exempt: Sequence[str]) -> str:
    """Slowest frontend first."""
    exempt_note = (
        f"; TUs under {', '.join(f'`{d}`' for d in exempt)} are reported, not gated" if exempt else ""
    )
    lines = [
        "### Compile cost per TU",
        "",
        f"gcc `-ftime-report` user CPU time per compile; the frontend's system time is shown apart and "
        f"not gated. {verdict(rows, budget)}{exempt_note}.",
        "",
        "| TU | frontend (s) | frontend sys (s) | backend (s) | total (s) | budget |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in sorted(rows, key=lambda r: r.phases.frontend, reverse=True):
        if not r.gated:
            status = "not gated"
        elif budget is None:
            status = "no budget"
        else:
            status = "**over**" if r.over(budget) else "ok"
        p = r.phases
        times = (p.frontend, p.frontend_sys, p.backend, p.total)
        lines.append(f"| {r.tu} | {' | '.join(f'{t:.3f}' for t in times)} | {status} |")
    return "\n".join(lines) + "\n"


async def _measure(steps: Sequence[CompileStep], digests: Sequence[Digest], sample: int) -> list[Phases]:
    results = await concurrently(
        execute_process(
            **implicitly(
                Process(
                    argv=(*step.argv, "-ftime-report"),
                    input_digest=digest,
                    working_directory=step.cwd or None,
                    env={**step.env, SAMPLE_ENV: str(sample)},
                    description=f"Measure the compile cost of {step.source} ({step.object})",
                    level=LogLevel.DEBUG,
                    # Every run measures afresh: a sample from a busier run must not stick.
                    cache_scope=ProcessCacheScope.PER_SESSION,
                )
            )
        )
        for step, digest in zip(steps, digests)
    )
    phases = []
    for step, result in zip(steps, results):
        stderr = result.stderr.decode(errors="replace")
        if result.exit_code != 0:
            raise ValueError(f"{step.owner}: compiling {step.source} failed:\n{stderr}")
        parsed = parse_time_report(stderr)
        if parsed is None:
            raise ValueError(
                f"{step.owner}: `{step.argv[0]}` printed no gcc `-ftime-report` phases for "
                f"{step.source}; `[compile-cost]` measures gcc compiles only"
            )
        phases.append(parsed)
    return phases


def check_exempt(
    address: Address, paths: Sequence[str], exempt_paths: Sequence[str], steps: Sequence[CompileStep]
) -> None:
    """An exempt path that exempts nothing is a typo, which would otherwise pass silently."""
    dirs = build_root_dirs(address, paths)
    for raw, d in zip(exempt_paths, build_root_dirs(address, exempt_paths)):
        if not under(d, dirs):
            raise ValueError(
                f"{address}: `{CompileCostExemptPathsField.alias}` entry {raw!r} is not under "
                f"`{CompileCostPathsField.alias}` {list(paths)}"
            )
        if not any(under(s.source, [d]) for s in steps):
            raise ValueError(
                f"{address}: `{CompileCostExemptPathsField.alias}` entry {raw!r} matches no measured "
                "translation unit"
            )


@rule(desc="Measure compile cost", level=LogLevel.DEBUG)
async def compile_cost_lint(
    request: CompileCostRequest.Batch, compile_cost: CompileCost, known: KnownPlatforms
) -> LintResult:
    (field_set,) = request.elements
    paths = field_set.paths.value or ()
    exempt_paths = field_set.exempt_paths.value or ()
    exempt = build_root_dirs(field_set.address, exempt_paths)
    steps = await steps_under(field_set.address, CompileCostPathsField.alias, paths, known)
    check_exempt(field_set.address, paths, exempt_paths, steps)
    inputs = await concurrently(compile_inputs(**implicitly({s.inputs: CompileInputsRequest})) for s in steps)
    # A compile writes its object, and a depfile beside it, into a directory its sandbox may not
    # have; nothing reads either back.
    object_dirs = await create_digest(
        CreateDigest(Directory(d) for d in sorted({os.path.dirname(s.object) for s in steps}) if d)
    )
    digests = await concurrently(merge_digests(MergeDigests([i.digest, object_dirs])) for i in inputs)
    phases = await _measure(steps, digests, sample=1)

    budget = compile_cost.frontend_budget
    gated = [not under(s.source, exempt) for s in steps]
    # Parallel compiles contend for the machine, so a gated TU over budget is measured once more,
    # one at a time, and the lower frontend counts.
    for i, p in enumerate(phases):
        if gated[i] and budget is not None and p.frontend > budget:
            (second,) = await _measure([steps[i]], [digests[i]], sample=2)
            phases[i] = lower(p, second)

    # A TU compiled several times is told apart by its object.
    compiles = Counter(s.source for s in steps)
    rows = [
        Row(tu=s.source if compiles[s.source] == 1 else f"{s.source} ({s.object})", phases=p, gated=g)
        for s, p, g in zip(steps, phases, gated)
    ]
    report = await create_digest(CreateDigest([FileContent(REPORT, render(rows, budget, exempt).encode())]))
    return LintResult(
        exit_code=1 if any(r.over(budget) for r in rows) else 0,
        stdout=summary(rows, budget),
        stderr="",
        linter_name=request.tool_name,
        partition_description=str(field_set.address),
        report=report,
    )


def rules():
    return [*collect_rules(), *CompileCostRequest.rules()]
