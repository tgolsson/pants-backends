"""Codegen dispatches on the class of the target's sources field, so each output type needs its
own sources field, target type, request and field set; `GenerateSpecialization` builds all four."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, make_dataclass

from pants.core.environments.target_types import EnvironmentField
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    GeneratedSources,
    GenerateSourcesRequest,
    SourcesField,
    Target,
)
from pants.engine.unions import UnionRule
from pants.option.global_options import GlobalOptions
from pants.util.memo import memoized_method

from pants_backend_generate.target_types import (
    GENERATE_FIELDS,
    GenerateFieldSet,
    GenerateOutputsField,
    GenerateSourcesField,
)
from pants_backend_generate.util_rules import formatting, generator
from pants_backend_generate.util_rules.formatting import FormatOutputsRequest
from pants_backend_generate.util_rules.generator import run_generator

logger = logging.getLogger(__name__)

_ALIASES: set[str] = set()
_warned_without_goal_backend = False


def _warn_without_goal_backend(global_options: GlobalOptions) -> None:
    global _warned_without_goal_backend
    if _warned_without_goal_backend or "pants_backend_generate" in global_options.backend_packages:
        return
    _warned_without_goal_backend = True
    logger.warning(
        "`pants generate` and the `generate` linter are unavailable until `pants_backend_generate` "
        "is added to `[GLOBAL].backend_packages`."
    )


@dataclass(frozen=True)
class GenerateSpecialization:
    """A `*_generate` target type whose outputs are `output` sources, formatted by `formatter`."""

    alias: str
    output: type[SourcesField]
    formatter: type[FormatOutputsRequest] | None = None
    help: str = ""

    sources_field_type: type[GenerateSourcesField] = field(init=False, repr=False)
    target_type: type[Target] = field(init=False, repr=False)
    request_type: type[GenerateSourcesRequest] = field(init=False, repr=False)
    field_set_type: type[GenerateFieldSet] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Otherwise the second target type silently shadows the first.
        if self.alias in _ALIASES:
            raise ValueError(f"A `GenerateSpecialization` with alias {self.alias!r} already exists.")
        _ALIASES.add(self.alias)
        camel = "".join(part.capitalize() for part in self.alias.split("_"))
        sources_field_type = type(
            f"{camel}SourcesField",
            (GenerateSourcesField,),
            {"output_type": self.output, "formatter_type": self.formatter},
        )
        target_type = type(
            f"{camel}Target",
            (Target,),
            {
                "alias": self.alias,
                "core_fields": (
                    *COMMON_TARGET_FIELDS,
                    sources_field_type,
                    *GENERATE_FIELDS,
                    EnvironmentField,
                ),
                "help": self.help,
            },
        )
        request_type = type(
            f"{camel}Request",
            (GenerateSourcesRequest,),
            {"input": sources_field_type, "output": self.output},
        )
        # `FieldSet.fields` uses `get_type_hints`: the annotation must be the class, not a name.
        field_set_type = make_dataclass(
            f"{camel}FieldSet",
            [("sources", sources_field_type)],
            bases=(GenerateFieldSet,),
            frozen=True,
            namespace={"required_fields": (sources_field_type, GenerateOutputsField)},
        )
        object.__setattr__(self, "sources_field_type", sources_field_type)
        object.__setattr__(self, "target_type", target_type)
        object.__setattr__(self, "request_type", request_type)
        object.__setattr__(self, "field_set_type", field_set_type)

    def target_types(self) -> tuple[type[Target], ...]:
        return (self.target_type,)

    # A second rule with the same canonical name would clash.
    @memoized_method
    def rules(self) -> tuple:
        @rule(
            canonical_name_suffix=self.alias,
            _param_type_overrides={"request": self.request_type},
        )
        async def generate(
            request: GenerateSourcesRequest, global_options: GlobalOptions
        ) -> GeneratedSources:
            _warn_without_goal_backend(global_options)
            return await run_generator(request)

        # The codegen path works without the goal backend enabled; the engine dedupes these.
        return (
            *generator.rules(),
            *formatting.rules(),
            *collect_rules({"generate": generate}),
            UnionRule(GenerateSourcesRequest, self.request_type),
            UnionRule(GenerateFieldSet, self.field_set_type),
        )
