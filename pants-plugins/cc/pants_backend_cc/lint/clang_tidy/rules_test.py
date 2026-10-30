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
