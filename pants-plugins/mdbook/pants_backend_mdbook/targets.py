"""

"""

from pants.engine.target import COMMON_TARGET_FIELDS, Dependencies, MultipleSourcesField, Target
from pants.util.strutil import softwrap


class MdBookDependencies(Dependencies):
    alias = "dependencies"

    help = softwrap("""Dependencies to include in the build.""")


class MdBookSources(MultipleSourcesField):
    alias = "sources"
    default = ("book.toml", "src/*", "src/**/*")

    help = softwrap("""The sources to use for a MdBook book, including the book.toml.""")


class MdBook(Target):
    alias = "md_book"
    core_fields = (*COMMON_TARGET_FIELDS, MdBookSources, MdBookDependencies)

    help = softwrap("""A build target for a MdBook book. """)


def targets():
    return [
        MdBook,
    ]
