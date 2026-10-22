from pants.util.frozendict import FrozenDict

from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER
from pants_backend_cmake.util_rules.scan import (
    is_compile_edge,
    is_header_like,
    parse_depfile,
    scan_argv,
    workspace_path,
)


def _edge(rule: str) -> NinjaEdge:
    return NinjaEdge(rule, ("out",), (), ("in",), (), (), FrozenDict(), None)


def test_is_compile_edge():
    assert is_compile_edge(_edge("CXX_COMPILER__demo_objects_unscanned_"))
    assert is_compile_edge(_edge("C_COMPILER__test_vec_unscanned_"))
    assert not is_compile_edge(_edge("CXX_SHARED_LIBRARY_LINKER__demo_shared_"))
    assert not is_compile_edge(_edge("phony"))


def test_is_header_like():
    assert is_header_like("src/a.h")
    assert is_header_like("src/a.hpp")
    assert is_header_like("include/vendored/core")
    assert not is_header_like("src/a.cpp")
    assert not is_header_like("CMakeLists.txt")


def test_workspace_path():
    assert workspace_path(f"{PLACEHOLDER}/src/a.cpp") == "src/a.cpp"
    assert workspace_path("CMakeFiles/x.dir/a.o") is None


def test_scan_argv():
    command = (
        "/usr/bin/c++ -DX -I../src -O3 -MD -MT CMakeFiles/a.o -MF CMakeFiles/a.o.d "
        "-o CMakeFiles/a.o -c ../src/a.cpp"
    )
    assert scan_argv(command.split()) == [
        "/usr/bin/c++",
        "-DX",
        "-I../src",
        "-O3",
        "../src/a.cpp",
        "-M",
        "-MG",
    ]


def test_parse_depfile():
    text = "CMakeFiles/a.o: ../src/a.cpp ../src/a.hpp \\\n  /usr/include/stdio.h \\\n  ../src/b.h\n"
    assert parse_depfile(text, "build") == ["src/a.cpp", "src/a.hpp", "src/b.h"]
    assert parse_depfile(text, "sub/build") == ["sub/src/a.cpp", "sub/src/a.hpp", "sub/src/b.h"]
