from pathlib import Path

import pytest

from pants_backend_cmake.ninja.parser import NinjaParseError, expand, parse_ninja, shell_escape

TESTDATA = Path(__file__).parent / "testdata"


@pytest.fixture(scope="module")
def graph():
    return parse_ninja(
        {
            "build.ninja": (TESTDATA / "build.ninja").read_text(),
            "CMakeFiles/rules.ninja": (TESTDATA / "rules.ninja").read_text(),
        }
    )


def test_expand_escapes_and_variables():
    assert expand("a$ b$:c$$d $x ${y}", {"x": "X", "y": "Y"}.get) == "a b:c$d X Y"


def test_shell_escape():
    assert shell_escape("CMakeFiles/a.dir/b.o") == "CMakeFiles/a.dir/b.o"
    assert shell_escape("a b") == "'a b'"
    assert shell_escape("it's") == "'it'\\''s'"


def test_small_manifest():
    graph = parse_ninja(
        {
            "build.ninja": "\n".join(
                [
                    "cflags =   -O2",
                    "rule cc",
                    "  # a comment inside the block",
                    "  command = gcc $cflags -c $in -o $out",
                    "  depfile = $out.d",
                    "  rspfile = $out.rsp",
                    "build out/a$ b.o | out/a.d: cc src/a$:x.c | src/a.h || phony_x",
                    "  cflags = -O0 $",
                    "     -g",
                    "build all: phony out/a$ b.o",
                    "default all",
                ]
            )
        }
    )
    edge = graph.edges[0]
    assert edge.rule == "cc"
    assert edge.outputs == ("out/a b.o",)
    assert edge.implicit_outputs == ("out/a.d",)
    assert edge.inputs == ("src/a:x.c",)
    assert edge.implicit_inputs == ("src/a.h",)
    assert edge.order_only == ("phony_x",)
    assert edge.bindings["cflags"] == "-O0 -g"
    assert graph.bindings["cflags"] == "-O2"
    assert graph.command(edge) == "gcc -O0 -g -c 'src/a:x.c' -o 'out/a b.o'"
    assert graph.command(edge, overrides={"cflags": "-Os"}) == "gcc -Os -c 'src/a:x.c' -o 'out/a b.o'"
    # File-name bindings are not shell-escaped, as in Ninja.
    assert graph.lookup(edge, "depfile", shell=False) == "out/a b.o.d"
    assert graph.lookup(edge, "rspfile", shell=False) == "out/a b.o.rsp"
    assert graph.edges[1].is_phony


def test_crlf_continuation_and_dollar_parity():
    graph = parse_ninja(
        {
            "build.ninja": "x = a$\r\n  b\r\ny = c$$\r\nz = d$$$\r\n  e\r\nbuild o: phony i\r\n",
        }
    )
    assert graph.bindings == {"x": "ab", "y": "c$", "z": "d$e"}
    assert graph.edges[0].inputs == ("i",)


def test_comment_is_not_continued():
    graph = parse_ninja({"build.ninja": "# foo$\nbuild o: phony i\n"})
    assert len(graph.edges) == 1


def test_trailing_escaped_space_is_kept():
    graph = parse_ninja({"build.ninja": "rule r\n  command = x$ \nbuild o: r i$ \n"})
    assert graph.edges[0].inputs == ("i ",)
    assert graph.command(graph.edges[0]) == "x "


def test_errors():
    with pytest.raises(NinjaParseError, match="not provided"):
        parse_ninja({"build.ninja": "include missing.ninja\n"})
    with pytest.raises(NinjaParseError, match="include cycle"):
        parse_ninja({"build.ninja": "include build.ninja\n"})
    with pytest.raises(NinjaParseError, match="multiple edges produce"):
        parse_ninja({"build.ninja": "rule r\n  command = x\nbuild o: r a\nbuild o: r b\n"})
    with pytest.raises(NinjaParseError, match="unknown rule"):
        parse_ninja({"build.ninja": "build o: nope a\n"})
    with pytest.raises(NinjaParseError, match="validation inputs"):
        parse_ninja({"build.ninja": "build o: phony a |@ v\n"})
    graph = parse_ninja({"build.ninja": "rule r\n  command = $command\nbuild o: r a\n"})
    with pytest.raises(NinjaParseError, match="cycle in rule"):
        graph.command(graph.edges[0])


def test_fixture_counts(graph):
    """A small demo project as configured by CMake, minus the regenerate and missing-input edges."""
    assert len(graph.rules) == 21
    assert len(graph.edges) == 51
    compile_edges = [e for e in graph.edges if "COMPILER" in e.rule]
    assert len(compile_edges) == 20
    assert graph.bindings["cmake_ninja_workdir"] == "__PANTS_CMAKE_ROOT__/build/"


def test_fixture_targets_and_kinds(graph):
    kinds = {}
    for name, kind in graph.target_kinds.items():
        kinds.setdefault(kind, []).append(name)
    assert len(kinds["EXECUTABLE"]) == 4
    assert kinds["STATIC_LIBRARY"] == ["demo"]
    assert kinds["SHARED_LIBRARY"] == ["demo_shared"]
    assert sorted(kinds["OBJECT_LIBRARY"]) == ["demo_cpu", "demo_objects"]
    assert sorted(kinds["UTILITY"]) == ["edit_cache", "gen_stubs", "gen_tables", "rebuild_cache", "test"]


def test_fixture_section_tagging(graph):
    # Custom commands, aliases and built-ins sit outside every target's sections.
    assert graph.producers["CMakeFiles/gen_tables"].target is None
    assert graph.producers["gen_tables"].target == "gen_tables"
    assert graph.producers["demo"].target is None
    assert graph.producers["all"].target is None
    for edge in graph.edges:
        if edge.target == "rebuild_cache":
            assert edge.rule == "phony" or edge.outputs == ("CMakeFiles/rebuild_cache.util",), edge


def test_fixture_compile_command(graph):
    edge = graph.producers["CMakeFiles/test_vec.dir/tests/vec_a.cpp.o"]
    assert edge.target == "test_vec"
    assert edge.inputs == ("__PANTS_CMAKE_ROOT__/tests/vec_a.cpp",)
    assert edge.order_only == ("cmake_object_order_depends_target_test_vec",)
    command = graph.command(edge)
    assert command.startswith("/usr/bin/c++ -DMINITEST_NO_COLOR -I__PANTS_CMAKE_ROOT__/tests/vendor")
    assert command.endswith(
        "-MD -MT CMakeFiles/test_vec.dir/tests/vec_a.cpp.o "
        "-MF CMakeFiles/test_vec.dir/tests/vec_a.cpp.o.d "
        "-o CMakeFiles/test_vec.dir/tests/vec_a.cpp.o "
        "-c __PANTS_CMAKE_ROOT__/tests/vec_a.cpp"
    )
    assert graph.lookup(edge, "depfile", shell=False) == "CMakeFiles/test_vec.dir/tests/vec_a.cpp.o.d"
    assert graph.lookup(edge, "deps") == "gcc"


def test_fixture_link_edges(graph):
    archive = graph.producers["libdemo.a"]
    assert archive.target == "demo"
    assert archive.order_only == ("demo_cpu", "demo_objects")
    assert "CMakeFiles/demo_objects.dir/src/vec.cpp.o" in archive.inputs
    assert graph.command(archive).startswith(": && cmake -E rm -f libdemo.a && /usr/bin/ar qc libdemo.a")
    test_vec = graph.producers["test_vec"]
    assert test_vec.implicit_inputs == ("libdemo.a",)
    assert test_vec.order_only == ("libdemo.a",)
