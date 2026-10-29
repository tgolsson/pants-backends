"""The consumer-probe derivation on a hand-written manifest, and the probes' absence on the
checked-in demo-project capture (made before probes existed)."""

from pathlib import Path

import pytest
from pants.util.frozendict import FrozenDict

from pants_backend_cc.util_rules.compile import ASM, CXX, C
from pants_backend_cc.util_rules.library import CcLibraryError
from pants_backend_cmake.ninja.graph import discover_targets
from pants_backend_cmake.ninja.parser import NinjaEdge, parse_ninja
from pants_backend_cmake.util_rules.link import (
    compile_flags,
    consumer_compile_flags,
    consumer_link,
    edge_language,
)

TESTDATA = Path(__file__).parent.parent / "ninja" / "testdata"

# A static C library with PRIVATE and PUBLIC requirements and its two consumer probes, as
# CMake generates them (the C++ probe through a response file). Paths are canonicalized.
MANIFEST = """\
rule C_COMPILER__lib_
  command = ${LAUNCHER}${CODE_CHECK}/usr/bin/cc $DEFINES $INCLUDES $FLAGS -MD -MT $out -MF $DEP_FILE -o $out -c $in
rule C_STATIC_LIBRARY_LINKER__lib_
  command = rm -f $TARGET_FILE && /usr/bin/ar qc $TARGET_FILE $LINK_FLAGS $in
rule C_COMPILER____pants_probe_lib_C_
  command = ${LAUNCHER}${CODE_CHECK}/usr/bin/cc $DEFINES $INCLUDES $FLAGS -MD -MT $out -MF $DEP_FILE -o $out -c $in
rule CXX_EXECUTABLE_LINKER____pants_probe_lib_C_
  command = $PRE_LINK && /usr/bin/c++ $FLAGS $LINK_FLAGS $in -o $TARGET_FILE $LINK_PATH $LINK_LIBRARIES && $POST_BUILD
rule CXX_COMPILER____pants_probe_lib_CXX_
  command = ${LAUNCHER}${CODE_CHECK}/usr/bin/c++ @$RSP_FILE -MD -MT $out -MF $DEP_FILE -o $out -c $in
  rspfile = $RSP_FILE
  rspfile_content = $DEFINES $INCLUDES $FLAGS
rule CXX_EXECUTABLE_LINKER____pants_probe_lib_CXX_
  command = $PRE_LINK && /usr/bin/c++ $FLAGS $LINK_FLAGS $in -o $TARGET_FILE $LINK_PATH $LINK_LIBRARIES && $POST_BUILD

# Object build statements for STATIC_LIBRARY target lib
build CMakeFiles/lib.dir/lib.c.o: C_COMPILER__lib_ __PANTS_CMAKE_ROOT__/lib.c
  DEFINES = -DLIB_PRIVATE=1 -DLIB_PUBLIC=1
  DEP_FILE = CMakeFiles/lib.dir/lib.c.o.d
  FLAGS = -O2 -Wall
  INCLUDES = -I__PANTS_CMAKE_ROOT__/include -I__PANTS_CMAKE_ROOT__/private

# Link build statements for STATIC_LIBRARY target lib
build liblib.a: C_STATIC_LIBRARY_LINKER__lib_ CMakeFiles/lib.dir/lib.c.o
  TARGET_FILE = liblib.a

# Object build statements for EXECUTABLE target __pants_probe_lib_C
build CMakeFiles/__pants_probe_lib_C.dir/__pants/probe.c.o: C_COMPILER____pants_probe_lib_C_ __PANTS_CMAKE_ROOT__/build/__pants/probe.c
  DEFINES = -DLIB_PUBLIC=1
  DEP_FILE = CMakeFiles/__pants_probe_lib_C.dir/__pants/probe.c.o.d
  FLAGS = -fPIE -O2 -Wall
  INCLUDES = -I__PANTS_CMAKE_ROOT__/include

# Link build statements for EXECUTABLE target __pants_probe_lib_C
build __pants_probe_lib_C: CXX_EXECUTABLE_LINKER____pants_probe_lib_C_ CMakeFiles/__pants_probe_lib_C.dir/__pants/probe.c.o | liblib.a
  FLAGS = -fno-rtti
  LINK_LIBRARIES = -Wl,-rpath,__PANTS_CMAKE_ROOT__/build  liblib.a  -lm
  POST_BUILD = :
  PRE_LINK = :
  TARGET_FILE = __pants_probe_lib_C

# Object build statements for EXECUTABLE target __pants_probe_lib_CXX
build CMakeFiles/__pants_probe_lib_CXX.dir/__pants/probe.cpp.o: CXX_COMPILER____pants_probe_lib_CXX_ __PANTS_CMAKE_ROOT__/build/__pants/probe.cpp
  DEFINES = -DLIB_PUBLIC=1
  DEP_FILE = CMakeFiles/__pants_probe_lib_CXX.dir/__pants/probe.cpp.o.d
  FLAGS = -std=gnu++20 -fPIE -O2
  INCLUDES = -I__PANTS_CMAKE_ROOT__/include
  RSP_FILE = CMakeFiles/__pants_probe_lib_CXX.dir/probe.cpp.o.rsp

# Link build statements for EXECUTABLE target __pants_probe_lib_CXX
build __pants_probe_lib_CXX: CXX_EXECUTABLE_LINKER____pants_probe_lib_CXX_ CMakeFiles/__pants_probe_lib_CXX.dir/__pants/probe.cpp.o | liblib.a
  FLAGS = -fno-rtti
  LINK_FLAGS = -rdynamic -Wl,--dependency-file=CMakeFiles/__pants_probe_lib_CXX.dir/link.d
  LINK_LIBRARIES = liblib.a  -lm
  POST_BUILD = :
  PRE_LINK = :
  TARGET_FILE = __pants_probe_lib_CXX
"""  # noqa: E501


@pytest.fixture(scope="module")
def graph():
    return parse_ninja({"build.ninja": MANIFEST})


@pytest.fixture(scope="module")
def targets(graph):
    return discover_targets(graph)


@pytest.fixture(scope="module")
def capture():
    graph = parse_ninja(
        {
            "build.ninja": (TESTDATA / "build.ninja").read_text(),
            "CMakeFiles/rules.ninja": (TESTDATA / "rules.ninja").read_text(),
        }
    )
    return graph, discover_targets(graph)


def test_edge_language():
    def edge(rule):
        return NinjaEdge(rule, ("o",), (), ("i",), (), (), FrozenDict(), None)

    assert edge_language(edge("C_COMPILER__x_")) == C
    assert edge_language(edge("CXX_EXECUTABLE_LINKER__x_")) == CXX
    assert edge_language(edge("ASM_NASM_COMPILER__x_")) == ASM
    assert edge_language(edge("CUSTOM_COMMAND")) is None


def test_probes_are_not_addressable(targets):
    assert targets["lib"].addressable
    assert not targets["__pants_probe_lib_C"].addressable
    assert not targets["__pants_probe_lib_CXX"].addressable


def test_consumer_compile_flags_are_the_probes_not_the_librarys(graph, targets):
    # PUBLIC define and include directory, the global flags; not the PRIVATE ones.
    assert consumer_compile_flags(graph, targets, "..", "lib", C) == (
        "-DLIB_PUBLIC=1",
        "-I../include",
        "-fPIE",
        "-O2",
        "-Wall",
    )
    # The C++ probe compiles through a response file, expanded in place.
    assert consumer_compile_flags(graph, targets, "..", "lib", CXX) == (
        "-DLIB_PUBLIC=1",
        "-I../include",
        "-std=gnu++20",
        "-fPIE",
        "-O2",
    )
    # For comparison, the library's own edge carries the private requirements.
    own = compile_flags(graph, graph.producers["CMakeFiles/lib.dir/lib.c.o"], "..")
    assert "-DLIB_PRIVATE=1" in own and "-I../private" in own


def test_consumer_link(graph, targets):
    # A C consumer of this library is linked with the C++ driver, CMake says.
    link = consumer_link(graph, targets, "..", "lib", C)
    assert link.driver == CXX
    assert link.flags == ("-fno-rtti",)
    assert link.libraries == ("-Wl,-rpath,../build", "liblib.a", "-lm")
    cxx = consumer_link(graph, targets, "..", "lib", CXX)
    assert cxx.flags == ("-fno-rtti", "-rdynamic")
    assert cxx.libraries == ("liblib.a", "-lm")


def test_missing_probe_is_an_error(capture):
    graph, targets = capture
    with pytest.raises(CcLibraryError, match="no consumer probe `__pants_probe_demo_C`"):
        consumer_compile_flags(graph, targets, "..", "demo", C)
