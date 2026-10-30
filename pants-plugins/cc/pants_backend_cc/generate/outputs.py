"""`DeclaredOutputs` for every target with generator `outputs`, so a build system can be configured
against them without running the generator."""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule

from pants_backend_cc.util_rules.outputs import DeclaredOutputs, DeclaredOutputsFieldSet
from pants_backend_generate.target_types import GenerateOutputsField


@dataclass(frozen=True)
class GeneratedOutputsFieldSet(DeclaredOutputsFieldSet):
    required_fields = (GenerateOutputsField,)

    outputs: GenerateOutputsField


@rule
async def generated_outputs(field_set: GeneratedOutputsFieldSet) -> DeclaredOutputs:
    return DeclaredOutputs(tuple(field_set.outputs.value or ()))


def rules():
    return [*collect_rules(), UnionRule(DeclaredOutputsFieldSet, GeneratedOutputsFieldSet)]
