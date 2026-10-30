"""`cc_tests`: one `cc_test` per directory of translation units."""

from __future__ import annotations

import os

from pants.engine.internals.graph import resolve_source_paths
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import GeneratedTargets, GenerateTargetsRequest, SourcesPathsRequest
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.logging import LogLevel

from pants_backend_cc.target_types import (
    CcTestsGeneratorTarget,
    CcTestSourcesField,
    CcTestsSourcesField,
    CcTestTarget,
    is_translation_unit,
)


class GenerateCcTestsRequest(GenerateTargetsRequest):
    generate_from = CcTestsGeneratorTarget


@rule(desc="Generate cc_test targets", level=LogLevel.DEBUG)
async def generate_cc_tests(
    request: GenerateCcTestsRequest, union_membership: UnionMembership
) -> GeneratedTargets:
    generator = request.generator
    paths = await resolve_source_paths(SourcesPathsRequest(generator[CcTestsSourcesField]), **implicitly())
    spec_path = generator.address.spec_path
    units_by_dir: dict[str, list[str]] = {}
    headers: list[str] = []
    for path in paths.files:
        relative = os.path.relpath(path, spec_path) if spec_path else path
        if is_translation_unit(relative):
            units_by_dir.setdefault(os.path.dirname(relative) or ".", []).append(relative)
        else:
            headers.append(relative)

    overrides = request.require_unparametrized_overrides()
    unknown = sorted(set(overrides) - set(units_by_dir))
    if unknown:
        raise ValueError(
            f"{generator.address}: `overrides` names tests that are not generated: {unknown}. "
            f"Generated: {sorted(units_by_dir)}"
        )
    targets = [
        CcTestTarget(
            {
                **request.template,
                CcTestSourcesField.alias: (*units, *headers),
                **overrides.get(name, {}),
            },
            request.template_address.create_generated(name),
            union_membership,
        )
        for name, units in sorted(units_by_dir.items())
    ]
    return GeneratedTargets(generator, targets)


def rules():
    return [*collect_rules(), UnionRule(GenerateTargetsRequest, GenerateCcTestsRequest)]
