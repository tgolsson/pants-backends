from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pants.engine.engine_aware import EngineAwareReturnType
from pants.engine.environment import EnvironmentName
from pants.engine.fs import Digest
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionMembership, union
from pants.util.logging import LogLevel
from pants.util.strutil import bullet_list


@dataclass(frozen=True)
class ImageBundleRequest:
    target: Target


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class FallibleImageBundleRequest:
    target: Target

    field_set_type: ClassVar[type[FieldSet]]


@dataclass(frozen=True)
class FallibleImageBundleRequestWrap:
    """A built OCI image layer."""

    request: FallibleImageBundleRequest


@dataclass(frozen=True)
class ImageBundle:
    """A built OCI image layer."""

    digest: Digest
    image_sha: str
    is_local: bool


@dataclass(frozen=True)
class FallibleImageBundle(EngineAwareReturnType):
    """Fallible version of `ImageBundlePackage` with error details."""

    output: ImageBundle | None
    exit_code: int = 0
    stdout: str | None = None
    stderr: str | None = None
    dependency_failed: bool = False

    def level(self) -> LogLevel:
        return LogLevel.ERROR if self.exit_code != 0 and not self.dependency_failed else LogLevel.DEBUG

    def message(self) -> str:
        message = self.import_path
        message += " succeeded." if self.exit_code == 0 else f" failed (exit code {self.exit_code})."
        if self.stdout:
            message += f"\n{self.stdout}"
        if self.stderr:
            message += f"\n{self.stderr}"
        return message

    def cacheable(self) -> bool:
        # Failed compile outputs should be re-rendered in every run.
        return self.exit_code == 0


@rule
def ibr_to_fibr(
    request: ImageBundleRequest, union_membership: UnionMembership
) -> FallibleImageBundleRequestWrap:
    tgt = request.target
    concrete_requests = [
        request_type(request_type.field_set_type.create(tgt))
        for request_type in union_membership[FallibleImageBundleRequest]
        if request_type.field_set_type.is_applicable(tgt)
    ]
    if len(concrete_requests) != 1:
        raise ValueError(
            f"Multiple or zero registered builders from {ImageBundleRequest.__name__} can "
            f"build target {tgt.address}. It is ambiguous which implementation to "
            "use.\n\n"
            "Possible implementations:\n\n"
            f"{bullet_list(sorted(generator.__class__.__name__ for generator in concrete_requests))}"
        )

    first_concrete = concrete_requests[0]

    return FallibleImageBundleRequestWrap(first_concrete)


def rules():
    return [
        *collect_rules(),
    ]
