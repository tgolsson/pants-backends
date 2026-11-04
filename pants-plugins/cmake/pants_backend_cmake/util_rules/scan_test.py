from pants.util.frozendict import FrozenDict

from pants_backend_cmake.ninja.parser import NinjaEdge
from pants_backend_cmake.util_rules.canonicalize import PLACEHOLDER
from pants_backend_cmake.util_rules.scan import is_compile_edge, workspace_path


def _edge(rule: str) -> NinjaEdge:
    return NinjaEdge(rule, ("out",), (), ("in",), (), (), FrozenDict(), None)


def test_is_compile_edge():
    assert is_compile_edge(_edge("CXX_COMPILER__demo_objects_unscanned_"))
    assert is_compile_edge(_edge("C_COMPILER__test_vec_unscanned_"))
    assert not is_compile_edge(_edge("CXX_SHARED_LIBRARY_LINKER__demo_shared_"))
    assert not is_compile_edge(_edge("phony"))


def test_workspace_path():
    assert workspace_path(f"{PLACEHOLDER}/src/a.cpp") == "src/a.cpp"
    assert workspace_path("CMakeFiles/x.dir/a.o") is None
