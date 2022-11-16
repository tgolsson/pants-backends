"""

"""

from pants.engine.target import COMMON_TARGET_FIELDS, MultipleSourcesField, Target
from pants.util.strutil import softwrap


class MdBookSources(MultipleSourcesField):
    alias = "sources"
    expected_file_extensions = (".toml", ".md", ".png", ".jpg", ".jpeg")
    default = ("book.toml", "src/*", "src/**/*")

    help = softwrap("""The sources to use for a MdBook book, including the book.toml.""")


class MdBook(Target):
    alias = "md_book"
    core_fields = (*COMMON_TARGET_FIELDS, MdBookSources)

    help = softwrap("""A build target for a MdBook book. """)


def targets():
    return [
        MdBook,
    ]
