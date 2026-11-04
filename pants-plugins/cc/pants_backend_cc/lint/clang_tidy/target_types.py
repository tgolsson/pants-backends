from __future__ import annotations

from pants.engine.target import StringSequenceField
from pants.util.strutil import softwrap


class ClangTidyPathsField(StringSequenceField):
    """A provider registers this on its target types; it lives apart from the rules so that
    registering it does not import `pants.backend.python`."""

    alias = "clang_tidy_paths"
    default = ()
    help = softwrap(
        """
        Directories, relative to this BUILD file, whose translation units `[clang-tidy]` lints
        and whose headers it reports diagnostics in (its `--header-filter`); none means the
        target is not linted. A translation unit compiled several times is linted once per
        compile, with each one's flags.
        """
    )
