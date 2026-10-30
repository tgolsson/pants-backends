"""The paths a target will produce, known without producing them, so a build system can be
configured against generated files before any generator runs."""

from __future__ import annotations

from abc import ABCMeta
from dataclasses import dataclass

from pants.engine.environment import EnvironmentName
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import union


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class DeclaredOutputsFieldSet(FieldSet, metaclass=ABCMeta):
    """A member's rule takes the field set and returns its `DeclaredOutputs` without running
    anything."""


@dataclass(frozen=True)
class DeclaredOutputs:
    paths: tuple[str, ...]
    """Relative to the build root."""


@rule(polymorphic=True)
async def declared_outputs(
    field_set: DeclaredOutputsFieldSet, environment_name: EnvironmentName
) -> DeclaredOutputs:
    raise NotImplementedError()


def rules():
    return collect_rules()
