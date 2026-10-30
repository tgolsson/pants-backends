"""Fields shared by every `*_generate` target; only `sources` is subclassed per specialization."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from pants.engine.addresses import Address
from pants.engine.env_vars import EXTRA_ENV_VARS_USAGE_HELP
from pants.engine.target import (
    FieldSet,
    IntField,
    InvalidFieldException,
    MultipleSourcesField,
    SourcesField,
    SpecialCasedDependencies,
    StringField,
    StringSequenceField,
    ValidNumbers,
)
from pants.engine.unions import union
from pants.util.strutil import help_text, softwrap

_GLOB_CHARACTERS = frozenset("*?[]{}!")


class GenerateRunnableField(StringField):
    alias: ClassVar[str] = "runnable"
    required = True
    help = help_text(
        """
        Address of the generator, a target `pants run` can run in a sandbox (such as a
        `pex_binary`). It runs at the build root.
        """
    )


class GenerateArgsField(StringSequenceField):
    alias: ClassVar[str] = "args"
    default = ()
    help = "Arguments passed to the `runnable`."


class GenerateSourcesField(MultipleSourcesField):
    alias: ClassVar[str] = "sources"
    required = False
    default = ()
    help = help_text(
        """
        Files the generator reads, relative to the BUILD file; present in the sandbox at their
        repo-relative paths.
        """
    )

    output_type: ClassVar[type[SourcesField]]
    formatter_type: ClassVar[type | None] = None


class GenerateOutputsField(StringSequenceField):
    alias: ClassVar[str] = "outputs"
    required = True
    help = help_text(
        """
        Every file the generator writes, exactly: build-root-relative, normalised paths, each
        once, no globs. A missing or an extra file is an error.
        """
    )

    @classmethod
    def compute_value(cls, raw_value: Iterable[str] | None, address: Address) -> tuple[str, ...]:
        value = super().compute_value(raw_value, address)
        if not value:
            raise InvalidFieldException(f"The `{cls.alias}` field of {address} must name at least one file.")
        seen: set[str] = set()
        for path in value:
            problem = _output_path_problem(path)
            if problem:
                raise InvalidFieldException(f"The `{cls.alias}` field of {address} has {path!r}: {problem}.")
            if path in seen:
                raise InvalidFieldException(
                    f"The `{cls.alias}` field of {address} lists {path!r} more than once."
                )
            seen.add(path)
        return value


def _output_path_problem(path: str) -> str | None:
    if not path:
        return "outputs must not be empty"
    if os.path.basename(path) in ("", "."):
        return "outputs are files, not directories"
    if os.path.isabs(path):
        return "outputs are paths relative to the build root"
    if _GLOB_CHARACTERS & set(path):
        return "outputs are exact paths, not globs"
    normalised = os.path.normpath(path)
    if normalised == ".":
        return "outputs are files, not directories"
    if normalised.split("/")[0] == "..":
        return "outputs must stay inside the build root"
    if normalised != path:
        return softwrap(
            f"""
            write it as {normalised!r} (outputs are compared as strings)
            """
        )
    return None


class GenerateExecutionDependenciesField(SpecialCasedDependencies):
    alias: ClassVar[str] = "execution_dependencies"
    help = help_text(
        """
        Targets whose transitive files are present in the sandbox at their repo-relative
        paths.
        """
    )


class GenerateRunnableDependenciesField(SpecialCasedDependencies):
    alias: ClassVar[str] = "runnable_dependencies"
    help = help_text(
        """
        Runnable targets put on the generator's `PATH` under their target name.
        """
    )


class GenerateExtraEnvVarsField(StringSequenceField):
    alias: ClassVar[str] = "extra_env_vars"
    help = help_text(
        f"""
        Additional environment variables for the generator.

        {EXTRA_ENV_VARS_USAGE_HELP}
        """
    )


class GenerateTimeoutField(IntField):
    alias: ClassVar[str] = "timeout"
    default = None
    valid_numbers = ValidNumbers.positive_only
    help = "Seconds the generator may run before it is killed. No limit by default."


GENERATE_FIELDS = (
    GenerateRunnableField,
    GenerateArgsField,
    GenerateOutputsField,
    GenerateExecutionDependenciesField,
    GenerateRunnableDependenciesField,
    GenerateExtraEnvVarsField,
    GenerateTimeoutField,
)


@union
@dataclass(frozen=True)
class GenerateFieldSet(FieldSet):
    """The `lint` goal instantiates this base directly, so `output_type` reads the field
    instance, not the annotation."""

    required_fields = (GenerateSourcesField, GenerateOutputsField)

    sources: GenerateSourcesField
    outputs: GenerateOutputsField

    @property
    def output_type(self) -> type[SourcesField]:
        return self.sources.output_type
