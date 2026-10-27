"""Formatting runs inside the codegen rule, because `fmt` never sees codegen output and `lint`
compares against the formatted bytes."""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.environment import EnvironmentName
from pants.engine.fs import Snapshot
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import union


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class FormatOutputsRequest:
    """Union base: a member's rule returns `FormattedOutputs` with exactly the files given."""

    snapshot: Snapshot
    address: Address


@dataclass(frozen=True)
class FormattedOutputs:
    snapshot: Snapshot


@rule(polymorphic=True)
async def format_outputs(request: FormatOutputsRequest, env_name: EnvironmentName) -> FormattedOutputs:
    raise NotImplementedError()


def rules():
    return collect_rules()
