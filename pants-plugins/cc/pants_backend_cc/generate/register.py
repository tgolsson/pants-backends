"""`cpp_generate`: an optional sub-backend, since its formatter is a pex and needs
`pants.backend.python`, which `pants_backend_cc` itself does not."""

from __future__ import annotations

from pants.backend.cc.target_types import CCSourceField

try:
    from pants_backend_cc.generate import clang_format, outputs
    from pants_backend_cc.generate.clang_format import ClangFormatOutputsRequest
    from pants_backend_generate.specialization import GenerateSpecialization
except ModuleNotFoundError as e:
    if e.name != "pants_backend_generate":
        raise
    raise ImportError(
        "`pants_backend_cc.generate` needs the `pants-backend-generate` distribution: install "
        "`pants_backend_cc[generate]` or add `pants-backend-generate` to `[GLOBAL].plugins`."
    ) from e

cpp_generate = GenerateSpecialization(
    "cpp_generate",
    output=CCSourceField,
    formatter=ClangFormatOutputsRequest,
    help="C/C++ sources written by a generator, formatted with `[clang-format]`.",
)


def target_types():
    return cpp_generate.target_types()


def rules():
    return [*cpp_generate.rules(), *clang_format.rules(), *outputs.rules()]
