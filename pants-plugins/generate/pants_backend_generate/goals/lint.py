"""The `generate` linter: committed outputs must match what their generator writes now."""

from __future__ import annotations

from pants.base.glob_match_error_behavior import GlobMatchErrorBehavior
from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import PathGlobs
from pants.engine.intrinsics import get_digest_contents, path_globs_to_digest
from pants.engine.rules import collect_rules, rule
from pants.util.logging import LogLevel

from pants_backend_generate.goals.generate import GenerateSubsystem, generate_outputs
from pants_backend_generate.target_types import GenerateFieldSet


class GenerateLintRequest(LintTargetsRequest):
    field_set_type = GenerateFieldSet
    tool_subsystem = GenerateSubsystem  # type: ignore[assignment]
    partitioner_type = PartitionerType.DEFAULT_ONE_PARTITION_PER_INPUT


async def _stale_outputs(field_set: GenerateFieldSet) -> list[str]:
    generated = await generate_outputs(field_set)
    committed_digest = await path_globs_to_digest(
        PathGlobs(
            field_set.outputs.value,
            glob_match_error_behavior=GlobMatchErrorBehavior.ignore,
        )
    )
    generated_contents = await get_digest_contents(generated.snapshot.digest)
    committed_contents = await get_digest_contents(committed_digest)
    expected = {fc.path: fc.content for fc in generated_contents}
    committed = {fc.path: fc.content for fc in committed_contents}
    return [
        f"{field_set.address}: {path}: {'missing' if path not in committed else 'stale'}"
        for path in sorted(expected)
        if committed.get(path) != expected[path]
    ]


@rule(desc="Check generated files are current", level=LogLevel.DEBUG)
async def lint_generated(request: GenerateLintRequest.Batch) -> LintResult:
    lines: list[str] = []
    for field_set in request.elements:
        lines.extend(await _stale_outputs(field_set))
    stderr = ""
    if lines:
        addresses = " ".join(sorted({str(field_set.address) for field_set in request.elements}))
        stderr = "\n".join(lines) + f"\n\nRun `pants generate {addresses}` to refresh."
    return LintResult(
        exit_code=1 if lines else 0,
        stdout="",
        stderr=stderr,
        linter_name=request.tool_name,
    )


def rules():
    return [*collect_rules(), *GenerateLintRequest.rules()]
