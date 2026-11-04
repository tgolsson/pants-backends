from __future__ import annotations

from pants_backend_generate.file_generate import file_generate
from pants_backend_generate.goals import generate, lint
from pants_backend_generate.util_rules import formatting, generator


def target_types():
    return file_generate.target_types()


def rules():
    return [
        *generator.rules(),
        *formatting.rules(),
        *generate.rules(),
        *lint.rules(),
        *file_generate.rules(),
    ]
