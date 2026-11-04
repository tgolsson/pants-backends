import re

from pants_backend_cc.lint.clang_tidy.rules import header_filter


def test_header_filter_in_a_build_directory():
    pattern = re.compile(header_filter("build", ["src"]))
    assert pattern.search("../src/a.h")
    assert pattern.search("/sandbox/build/../src/a.h")
    assert not pattern.search("../other/a.h")


def test_header_filter_in_the_sandbox_root():
    pattern = re.compile(header_filter("", ["src"]).replace("{chroot}", "/sandbox"))
    assert pattern.search("src/a.h")
    assert pattern.search("./src/a.h")
    assert pattern.search("/sandbox/src/a.h")
    assert not pattern.search("/usr/src/linux/a.h")
    assert not pattern.search("other/src/a.h")


def test_header_filter_of_the_compile_directory_itself():
    # A root BUILD selecting `.`: every relative path, never a system header.
    pattern = re.compile(header_filter("", ["."]).replace("{chroot}", "/sandbox"))
    assert pattern.search("src/a.h")
    assert pattern.search("./src/a.h")
    assert pattern.search("/sandbox/src/a.h")
    assert pattern.search("a.h")
    assert not pattern.search("/usr/include/a.h")
    # Compiling in `build` with `build` selected: paths relative to it, not `../src`.
    pattern = re.compile(header_filter("build", ["build"]))
    assert pattern.search("gen/a.h")
    assert pattern.search("/sandbox/build/gen/a.h")
    assert not pattern.search("/usr/include/a.h")
