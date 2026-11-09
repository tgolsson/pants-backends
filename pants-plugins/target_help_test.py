"""`pants help` renders every target type of every backend in this repo.

Help calls `typing.get_type_hints` on each field's `compute_value`, so an annotation naming a type
imported only under `TYPE_CHECKING` crashes `pants help targets` and `help-all`.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from pants.engine.target import Target
from pants.engine.unions import UnionMembership, UnionRule
from pants.help.help_info_extracter import TargetTypeHelpInfo

# Parked and not enabled in `pants.toml`.
SKIPPED = {"pants_backend_odin"}


def _register_modules() -> list[str]:
    # Found from the `:register_files` glob, so a backend missing from the test's dependencies fails
    # to import instead of being silently skipped.
    paths = sorted(Path("pants-plugins").glob("*/pants_backend_*/**/register.py"))
    assert paths, "no register.py files in the sandbox"
    # pants-plugins/<plugin>/pants_backend_x/.../register.py -> pants_backend_x....register
    modules = [".".join(Path(*p.parts[2:]).with_suffix("").parts) for p in paths]
    return [m for m in modules if m.split(".")[0] not in SKIPPED]


def _load() -> tuple[list[type[Target]], UnionMembership]:
    target_types: list[type[Target]] = []
    union_rules: list[UnionRule] = []
    for name in _register_modules():
        try:
            module = importlib.import_module(name)
        except ImportError as e:
            raise ImportError(
                f"Cannot import {name}: add its register.py to the `tests` dependencies in "
                "pants-plugins/BUILD."
            ) from e
        if hasattr(module, "target_types"):
            target_types.extend(module.target_types())
        if hasattr(module, "rules"):
            union_rules.extend(r for r in module.rules() if isinstance(r, UnionRule))
    return target_types, UnionMembership.from_rules(union_rules)


TARGET_TYPES, UNION_MEMBERSHIP = _load()


def test_found_backends() -> None:
    modules = _register_modules()
    assert "pants_backend_oci.register" in modules
    assert "pants_backend_cc.generate.register" in modules


@pytest.mark.parametrize("target_type", TARGET_TYPES, ids=lambda t: t.alias)
def test_target_help(target_type: type[Target]) -> None:
    TargetTypeHelpInfo.create(
        target_type, provider="", union_membership=UNION_MEMBERSHIP, get_field_type_provider=None
    )
