"""What runs containers, for `pants run`, OCI repl and scripted build steps.

Each runner implements the unions here with members naming it in `runner`, and `[oci].runner`
picks one: podman, from this backend, or Docker, from `pants_backend_oci.docker`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, TypeVar

from pants.engine.addresses import Address
from pants.engine.environment import EnvironmentName
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionMembership, union

from pants_backend_oci.subsystem import OciRunner, OciSubsystem
from pants_backend_oci.util_rules.image_bundle import ImageBundle


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class ContainerBuildRequest:
    """Run a scripted build step's builder with `args` on `bundle`'s layout."""

    bundle: ImageBundle
    args: tuple[str, ...]
    description: str
    output_files: tuple[str, ...] = ()
    output_directories: tuple[str, ...] = ()

    runner: ClassVar[OciRunner]


@rule(polymorphic=True)
async def container_build(request: ContainerBuildRequest) -> ProcessResult:
    raise NotImplementedError()


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class ContainerRunRequest:
    """The process running `bundle`'s image outside the sandbox, for `pants run` and repl."""

    bundle: ImageBundle
    address: Address
    interactive: bool = False
    tty: bool = False

    runner: ClassVar[OciRunner]


@rule(polymorphic=True)
async def container_run(request: ContainerRunRequest) -> Process:
    raise NotImplementedError()


_R = TypeVar("_R")


def runner_member(base: type[_R], oci: OciSubsystem, union_membership: UnionMembership) -> type[_R]:
    """The member of `base` for `[oci].runner`."""
    for member in union_membership.get(base):
        if member.runner == oci.runner:  # type: ignore[attr-defined]
            return member
    raise ValueError(
        f'`[oci].runner = "{oci.runner.value}"` needs the `pants.backend.docker` and'
        " `pants_backend_oci.docker` backends in `[GLOBAL].backend_packages`."
    )


def rules():
    return collect_rules()
