from pathlib import Path

import pytest

from pants_backend_cmake.ninja.graph import UnsupportedGraphError, discover_targets, plan_target
from pants_backend_cmake.ninja.parser import parse_ninja

TESTDATA = Path(__file__).parent / "testdata"


@pytest.fixture(scope="module")
def graph():
    return parse_ninja(
        {
            "build.ninja": (TESTDATA / "build.ninja").read_text(),
            "CMakeFiles/rules.ninja": (TESTDATA / "rules.ninja").read_text(),
        }
    )


@pytest.fixture(scope="module")
def targets(graph):
    return discover_targets(graph)


def test_discovered_targets(targets):
    addressable = [t.name for t in targets.values() if t.addressable]
    assert sorted(addressable) == ["demo", "demo_shared", "test_vec"]
    assert targets["demo"].kind == "STATIC_LIBRARY"
    assert targets["demo"].final_outputs == ("libdemo.a",)
    assert targets["demo_shared"].final_outputs == ("libdemo_shared.so",)
    assert targets["test_vec"].kind == "EXECUTABLE"
    assert targets["test_vec"].final_outputs == ("test_vec",)
    assert not targets["demo_objects"].addressable
    assert targets["demo_objects"].final_outputs == ()
    assert targets["gen_tables"].final_outputs == ()


def _outputs(plan):
    return {e.outputs[0] for e in plan.edges}


def test_plan_static_library(graph, targets):
    plan = plan_target(graph, "demo", targets)
    assert plan.dependencies == ()
    assert plan.outputs == ("libdemo.a",)
    # The object library's TUs come in through the archive's inputs, beside demo's own.
    assert _outputs(plan) == {
        "libdemo.a",
        "CMakeFiles/demo.dir/src/api.cpp.o",
        "CMakeFiles/demo_objects.dir/src/vec.cpp.o",
        "CMakeFiles/demo_objects.dir/src/str.cpp.o",
    }
    assert "__PANTS_CMAKE_ROOT__/src/vec.cpp" in plan.sources
    assert all(s.startswith("__PANTS_CMAKE_ROOT__/") for s in plan.sources)


def test_plan_shared_library_shares_objects(graph, targets):
    static = plan_target(graph, "demo", targets)
    shared = plan_target(graph, "demo_shared", targets)
    objects = {e for e in static.edges if e.target == "demo_objects"}
    assert len(objects) == 2
    assert {e for e in shared.edges if e.target == "demo_objects"} == objects
    assert shared.outputs == ("libdemo_shared.so",)


def test_plan_test_executable_stops_at_library(graph, targets):
    plan = plan_target(graph, "test_vec", targets)
    assert plan.dependencies == ("demo",)
    assert _outputs(plan) == {
        "test_vec",
        "CMakeFiles/test_vec.dir/tests/vec_a.cpp.o",
        "CMakeFiles/test_vec.dir/tests/vendor/minitest.c.o",
    }
    assert "__PANTS_CMAKE_ROOT__/tests/vendor/minitest.c" in plan.sources
    # Nothing of demo's own compile edges leaks into the test's plan.
    assert all(e.target == "test_vec" for e in plan.edges)


def test_every_addressable_target_plans(graph, targets):
    for info in targets.values():
        if info.addressable:
            plan = plan_target(graph, info.name, targets)
            assert not any(e.is_phony for e in plan.edges), info.name
            assert {graph.producers[o] for o in info.final_outputs} <= set(plan.edges), info.name


MINI = """\
rule CC
  command = cc -c $in -o $out
rule C_EXECUTABLE_LINKER__x_
  command = cc $in -o $out
rule C_SHARED_LIBRARY_LINKER__sh_
  command = cc -shared $in -o $out
rule CMAKE_SYMLINK_LIBRARY
  command = cmake -E cmake_symlink_library $in $SONAME $out
rule CUSTOM_COMMAND
  command = $COMMAND

# Object build statements for SHARED_LIBRARY target sh
build CMakeFiles/sh.dir/sh.c.o: CC __PANTS_CMAKE_ROOT__/sh.c

# Link build statements for SHARED_LIBRARY target sh
build libsh.so.1.2.3: C_SHARED_LIBRARY_LINKER__sh_ CMakeFiles/sh.dir/sh.c.o
build libsh.so.1 libsh.so: CMAKE_SYMLINK_LIBRARY libsh.so.1.2.3
  SONAME = libsh.so.1

# Object build statements for EXECUTABLE target app
build CMakeFiles/app.dir/app.c.o: CC __PANTS_CMAKE_ROOT__/app.c

# Link build statements for EXECUTABLE target app
build app: C_EXECUTABLE_LINKER__x_ CMakeFiles/app.dir/app.c.o | libsh.so.1.2.3

# Object build statements for STATIC_LIBRARY target core
build cmake_object_order_depends_target_core: phony || gen.h
build CMakeFiles/core.dir/core.c.o: CC __PANTS_CMAKE_ROOT__/core.c || cmake_object_order_depends_target_core

# Link build statements for STATIC_LIBRARY target core
build libcore.a: C_EXECUTABLE_LINKER__x_ CMakeFiles/core.dir/core.c.o

# Custom command for gen.h
build gen.h: CUSTOM_COMMAND
  COMMAND = echo > gen.h

# Target aliases.
build sh: phony libsh.so
"""


def test_versioned_shared_library():
    graph = parse_ninja({"build.ninja": MINI})
    targets = discover_targets(graph)
    assert targets["sh"].final_outputs == ("libsh.so.1.2.3", "libsh.so.1", "libsh.so")
    assert targets["app"].final_outputs == ("app",)
    app = plan_target(graph, "app", targets)
    assert app.dependencies == ("sh",)
    assert {e.outputs for e in app.edges} == {("CMakeFiles/app.dir/app.c.o",), ("app",)}
    sh = plan_target(graph, "sh", targets)
    assert {e.outputs for e in sh.edges} == {
        ("CMakeFiles/sh.dir/sh.c.o",),
        ("libsh.so.1.2.3",),
        ("libsh.so.1", "libsh.so"),
    }


def test_generated_header_via_order_only_is_an_error():
    graph = parse_ninja({"build.ninja": MINI})
    targets = discover_targets(graph)
    with pytest.raises(UnsupportedGraphError, match="gen.h"):
        plan_target(graph, "core", targets)
