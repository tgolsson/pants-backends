from __future__ import annotations

from pants.core.target_types import FileSourceField

from pants_backend_generate.specialization import GenerateSpecialization

file_generate = GenerateSpecialization(
    "file_generate",
    output=FileSourceField,
    help="Files written by a generator, kept as written.",
)
