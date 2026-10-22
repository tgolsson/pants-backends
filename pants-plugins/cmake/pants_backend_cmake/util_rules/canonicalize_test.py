import pytest

from pants_backend_cmake.util_rules.canonicalize import (
    PLACEHOLDER,
    CanonicalizeError,
    canonicalize,
    canonicalize_build_ninja,
    rebase,
    sandbox_root,
    tool_paths,
)

ROOT = "/tmp/pants-sandbox-abc"
CMAKE = "/tmp/immutable_inputs/abc/cmake-3.31.12-linux-x86_64/bin/cmake"
CCMAKE = "/tmp/immutable_inputs/abc/cmake-3.31.12-linux-x86_64/bin/ccmake"
NINJA = "/tmp/pants-sandbox-abc/__ninja/ninja"
TOOLS = {CMAKE: "cmake", CCMAKE: "ccmake", NINJA: "ninja"}

CACHE = f"""\
CMAKE_COMMAND:INTERNAL={CMAKE}
CMAKE_CPACK_COMMAND:INTERNAL=/tmp/immutable_inputs/abc/cmake-3.31.12-linux-x86_64/bin/cpack
CMAKE_CTEST_COMMAND:INTERNAL=/tmp/immutable_inputs/abc/cmake-3.31.12-linux-x86_64/bin/ctest
CMAKE_EDIT_COMMAND:INTERNAL={CCMAKE}
CMAKE_MAKE_PROGRAM:FILEPATH={NINJA}
CMAKE_AR:FILEPATH=/usr/bin/ar
"""

RAW = f"""\
cmake_ninja_workdir = {ROOT}/build/

build CMakeFiles/x.dir/a.c.o: C_COMPILER__x_ {ROOT}/src/a.c
  INCLUDES = -I{ROOT}/src

build build.ninja: RERUN_CMAKE | {ROOT}/CMakeLists.txt /usr/share/cmake/Modules/X.cmake
  pool = console

# A missing CMake input file is not an error.
build {ROOT}/CMakeLists.txt /usr/share/cmake/Modules/X.cmake: phony

build cmake_object_order_depends_target_x: phony || CMakeFiles/x.dir

build empty: phony

build all: phony CMakeFiles/x.dir/a.c.o

build CMakeFiles/rebuild_cache.util: CUSTOM_COMMAND
  COMMAND = cd {ROOT}/build && {CMAKE} --regenerate-during-build -S{ROOT} -B{ROOT}/build

build CMakeFiles/edit_cache.util: CUSTOM_COMMAND
  COMMAND = cd {ROOT}/build && {CCMAKE} -S{ROOT} -B{ROOT}/build
"""


def test_sandbox_root():
    assert sandbox_root(RAW, "build") == ROOT
    assert sandbox_root(RAW.replace("/build/", "/build"), "build") == ROOT
    with pytest.raises(CanonicalizeError):
        sandbox_root(RAW, "other")
    with pytest.raises(CanonicalizeError):
        sandbox_root("x = 1\n", "build")
    with pytest.raises(CanonicalizeError):
        sandbox_root("cmake_ninja_workdir = /build/\n", "build")
    with pytest.raises(CanonicalizeError):
        sandbox_root("cmake_ninja_workdir = /x/\n", ".")


def test_tool_paths():
    assert tool_paths(CACHE) == {
        CMAKE: "cmake",
        "/tmp/immutable_inputs/abc/cmake-3.31.12-linux-x86_64/bin/cpack": "cpack",
        "/tmp/immutable_inputs/abc/cmake-3.31.12-linux-x86_64/bin/ctest": "ctest",
        CCMAKE: "ccmake",
        NINJA: "ninja",
    }
    assert tool_paths("CMAKE_MAKE_PROGRAM:FILEPATH=CMAKE_MAKE_PROGRAM-NOTFOUND\n") == {}


def test_canonicalize_build_ninja():
    out = canonicalize_build_ninja(RAW, ROOT, TOOLS)
    assert ROOT not in out
    assert "immutable_inputs" not in out
    assert "RERUN_CMAKE" not in out
    assert "pool = console" not in out
    assert "/usr/share/cmake" not in out
    assert f"cmake_ninja_workdir = {PLACEHOLDER}/build/" in out
    assert f"-I{PLACEHOLDER}/src" in out
    assert (
        f"cd {PLACEHOLDER}/build && cmake --regenerate-during-build -S{PLACEHOLDER} -B{PLACEHOLDER}/build"
        in out
    )
    assert f"&& ccmake -S{PLACEHOLDER}" in out
    # Input-less phony edges that are not the missing-input edge survive.
    assert "build empty: phony\n" in out
    assert "build cmake_object_order_depends_target_x: phony || CMakeFiles/x.dir" in out
    assert "build all: phony CMakeFiles/x.dir/a.c.o" in out


def test_canonicalize_exact_tool_paths_only():
    # Quoted and prefixed occurrences are replaced exactly; look-alike paths are left alone.
    text = f'add_test([=[t]=] "{CMAKE}" "-E" "true") -DCMAKE_COMMAND={CMAKE} /opt/x/bin/cmake-lint'
    out = canonicalize(text, "/nope", TOOLS)
    assert out == 'add_test([=[t]=] "cmake" "-E" "true") -DCMAKE_COMMAND=cmake /opt/x/bin/cmake-lint'
    with pytest.raises(CanonicalizeError):
        canonicalize("x", "", TOOLS)


def test_rebase():
    assert rebase(f"-I{PLACEHOLDER}/src -c {PLACEHOLDER}/src/a.c", "..") == "-I../src -c ../src/a.c"
