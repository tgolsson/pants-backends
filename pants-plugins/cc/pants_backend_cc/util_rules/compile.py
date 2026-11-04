"""The compile steps of a C/C++ provider, so tools that work per translation unit (linters,
analyses) run over any provider's targets with the provider's own commands and sandboxes."""

from __future__ import annotations

from abc import ABCMeta
from dataclasses import dataclass

from pants.engine.addresses import Address
from pants.engine.collection import Collection
from pants.engine.environment import EnvironmentName
from pants.engine.fs import Digest
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import union
from pants.util.frozendict import FrozenDict

from pants_backend_platforms.util_rules.platforms import TargetPlatform

C = "c"
CXX = "cxx"
ASM = "asm"


@union(in_scope_types=[EnvironmentName])
class CompileInputsRequest(metaclass=ABCMeta):
    """A member is a provider's frozen dataclass naming one step's sandbox; its rule takes it and
    returns `CompileInputs`. Listing steps never builds a sandbox, so a member defers that work."""


@dataclass(frozen=True)
class CompileInputs:
    digest: Digest
    """Everything the compile reads, at sandbox-root-relative paths."""


@rule(polymorphic=True)
async def compile_inputs(request: CompileInputsRequest, environment_name: EnvironmentName) -> CompileInputs:
    raise NotImplementedError()


@dataclass(frozen=True)
class CompileStep:
    owner: Address
    source: str
    """The translation unit, relative to the sandbox root."""
    generated: bool
    """The provider's build writes `source`, so it is not a workspace file to lint."""
    object: str
    """The object file, relative to the sandbox root."""
    language: str
    """`c`, `cxx` or `asm`."""
    argv: tuple[str, ...]
    """The complete compile command, compiler first, run in `cwd`."""
    cwd: str
    """Relative to the sandbox root; "" is the root."""
    env: FrozenDict[str, str]
    inputs: CompileInputsRequest
    """Pass to `compile_inputs` for the sandbox `argv` runs in."""


class CompileSteps(Collection[CompileStep]):
    pass


@union(in_scope_types=[EnvironmentName, TargetPlatform])
@dataclass(frozen=True)
class CompileStepsFieldSet(FieldSet, metaclass=ABCMeta):
    """A member's rule takes the field set and a `TargetPlatform` and returns the target's
    `CompileSteps` for that platform."""


@rule(polymorphic=True)
async def compile_steps(
    field_set: CompileStepsFieldSet, platform: TargetPlatform, environment_name: EnvironmentName
) -> CompileSteps:
    raise NotImplementedError()


def rules():
    return collect_rules()
