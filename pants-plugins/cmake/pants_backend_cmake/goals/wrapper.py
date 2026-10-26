"""A hook for running a `cmake_test` executable under a wrapper, such as an emulator or a
tracer. Another backend subclasses `CMakeTestWrapperRequest`, implements `is_applicable` and a
rule from its subclass to `CMakeTestWrapper`, and registers the subclass with
`UnionRule(CMakeTestWrapperRequest, ...)`. The test goal then runs `argv_prefix` before the
test executable, with the wrapper's digests mounted and its env set. A test no member applies
to runs unwrapped. Wrappers do not compose: at most one member may apply to a test. A wrapper
that cannot be set up (its rule raises `CMakeTestWrapperError`, or a process it runs fails)
makes that test a failed test, not a crashed goal; any other exception from the rule is a bug
in the wrapper and propagates with its traceback, as does one from `is_applicable`."""

from __future__ import annotations

from dataclasses import dataclass, field

from pants.engine.environment import EnvironmentName
from pants.engine.fs import Digest
from pants.engine.process import ProcessExecutionFailure
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import Target
from pants.engine.unions import UnionMembership, union
from pants.util.frozendict import FrozenDict


class CMakeTestWrapperError(Exception):
    """No single wrapper applies to a test, or setting the wrapper up failed."""


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class CMakeTestWrapperRequest:
    target: Target

    @classmethod
    def is_applicable(cls, target: Target) -> bool:
        """Whether this member wraps `target`. At most one member may apply to a test."""
        raise NotImplementedError


@dataclass(frozen=True)
class CMakeTestWrapper:
    argv_prefix: tuple[str, ...] = ()
    """Run before the test's own argv. `{chroot}` expands to the sandbox root, which is where
    `immutable_input_digests` are mounted; the test runs from the build directory below it."""
    immutable_input_digests: FrozenDict[str, Digest] = field(default_factory=FrozenDict)
    """Mounted (symlinked from a shared cache, not copied) at these paths relative to the sandbox
    root. A path must not collide with a top-level path of the test's own inputs, such as the
    build directory or a data file's directory; a `__`-prefixed name is safe."""
    env: FrozenDict[str, str] = field(default_factory=FrozenDict)
    """Set on the test process, over the test's own env."""


@rule(polymorphic=True)
async def wrap_cmake_test(request: CMakeTestWrapperRequest, env_name: EnvironmentName) -> CMakeTestWrapper:
    raise NotImplementedError()


async def cmake_test_wrapper(target: Target, union_membership: UnionMembership) -> CMakeTestWrapper:
    """The wrapper of the one member that applies to `target`, or an empty wrapper."""
    members = [m for m in union_membership.get(CMakeTestWrapperRequest) if m.is_applicable(target)]
    if not members:
        return CMakeTestWrapper()
    if len(members) > 1:
        names = sorted(m.__name__ for m in members)
        raise CMakeTestWrapperError(f"{target.address}: more than one test wrapper applies: {names}")
    member = members[0]
    try:
        return await wrap_cmake_test(**implicitly({member(target): CMakeTestWrapperRequest}))
    except (CMakeTestWrapperError, ProcessExecutionFailure) as error:
        raise CMakeTestWrapperError(f"{target.address}: {member.__name__} failed: {error}") from error


def rules():
    return collect_rules()
