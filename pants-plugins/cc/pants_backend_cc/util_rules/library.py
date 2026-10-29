"""How a consumer, such as a `cc_test`, compiles against and links a library from any backend.

A library's backend implements three rules on its field set: where consumers must work
(`library_cwd`), and, for one consumer language and working directory, what a compile needs
(`compile_interface`) and what a link needs (`link_interface`). The compile half builds nothing,
so consumers compile while the library is still being built.
"""

from __future__ import annotations

from abc import ABCMeta
from dataclasses import dataclass

from pants.engine.environment import EnvironmentName
from pants.engine.fs import Digest
from pants.engine.rules import collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import union

from pants_backend_platforms.util_rules.platforms import TargetPlatform


class CcLibraryError(Exception):
    """A library cannot be consumed as asked; a test depending on it fails instead of crashing
    the goal."""


@dataclass(frozen=True)
class CcConsumer:
    language: str
    """`c` or `cxx`."""
    cwd: str | None
    """Where the consumer compiles and links, relative to the sandbox root ("" is the root);
    every path an interface gives is relative to it. `None` while `library_cwd` decides it."""


@union(in_scope_types=[EnvironmentName, TargetPlatform, CcConsumer])
@dataclass(frozen=True)
class CcLibraryFieldSet(FieldSet, metaclass=ABCMeta):
    """A library's field set. A member implements `library_cwd`, `compile_interface` and
    `link_interface`, raising `CcLibraryError` for a consumer it cannot serve. `library_cwd`
    must not read `consumer.cwd`, which is `None` then, nor vary with `consumer.language`: it is
    asked with the link language and its answer holds for every language.

    A library's outputs (`generated`, `libraries`, `runtime`) must be at paths unique to it,
    such as under its own build directory: consumers merge every library's, and two different
    files at one path fail the consumer. Consumers concatenate every library's flags and link
    arguments without deduplicating, so objects or whole-archive spans that several libraries
    bring are repeated, and may define a symbol twice."""


@dataclass(frozen=True)
class CcLibraryCwd:
    path: str | None
    """The directory consumers must compile and link in, relative to the sandbox root, because
    the library's flags and paths are relative to it; `None` when any will do."""


@dataclass(frozen=True)
class CcCompileInterface:
    flags: tuple[str, ...]
    headers: Digest
    """What a consumer's header scan reads: the library's header-like and generated files."""
    files: Digest
    """The files an include may resolve to, at `file_paths`; a compile gets those its scan
    finds."""
    file_paths: tuple[str, ...]
    generated: Digest
    """Read by every consumer compile, such as configure-time headers."""


@dataclass(frozen=True)
class CcLinkInterface:
    driver: str
    """The language whose compiler links the consumer."""
    flags: tuple[str, ...]
    """Before the consumer's objects."""
    args: tuple[str, ...]
    """After the consumer's objects: the library, everything it links against, system
    libraries, in the order the linker needs them."""
    libraries: Digest
    """The files the link reads, relative to the sandbox root."""
    runtime: Digest
    """What a linked executable needs at run time, relative to the sandbox root."""
    runtime_dirs: tuple[str, ...]
    """Directories holding shared libraries the executable loads, for its run path, relative to
    the consumer's `cwd`."""


@rule(polymorphic=True)
async def library_cwd(
    field_set: CcLibraryFieldSet,
    platform: TargetPlatform,
    consumer: CcConsumer,
    environment_name: EnvironmentName,
) -> CcLibraryCwd:
    raise NotImplementedError()


@rule(polymorphic=True)
async def compile_interface(
    field_set: CcLibraryFieldSet,
    platform: TargetPlatform,
    consumer: CcConsumer,
    environment_name: EnvironmentName,
) -> CcCompileInterface:
    raise NotImplementedError()


@rule(polymorphic=True)
async def link_interface(
    field_set: CcLibraryFieldSet,
    platform: TargetPlatform,
    consumer: CcConsumer,
    environment_name: EnvironmentName,
) -> CcLinkInterface:
    raise NotImplementedError()


def rules():
    return collect_rules()
