# Copyright 2026 Tom Solberg.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

"""Running the plugin's pure builder modules in a sandboxed process.

Rules only decide what to build; the work itself happens in a process, by a stdlib-only module
copied into the sandbox and run with the Python Pants provides for scripts.
"""

from __future__ import annotations

from importlib import resources
from types import ModuleType

from pants.engine.fs import CreateDigest, FileContent


def sandboxed_modules(*modules: ModuleType) -> CreateDigest:
    """The modules at their package paths, with empty `__init__.py`s for the packages, so their
    imports of each other still resolve."""
    files = []
    packages = set()
    for module in modules:
        package, _, name = module.__name__.rpartition(".")
        # Read through `importlib.resources` so this also works when the plugin is imported from a zip.
        source = resources.files(package).joinpath(f"{name}.py").read_bytes()
        files.append(FileContent(f"{module.__name__.replace('.', '/')}.py", source))
        parts = package.split(".")
        packages.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    files.extend(FileContent(f"{package}/__init__.py", b"") for package in sorted(packages))
    return CreateDigest(files)


def module_main(module: ModuleType) -> str:
    """A `python -c` program that runs `module.main` with the remaining arguments."""
    return f"import sys, {module.__name__} as m; sys.exit(m.main(sys.argv[1:]))"
