from pants_backend_cc.util_rules.compile import CXX, C
from pants_backend_cc.util_rules.executable import language


def test_language():
    assert language("a.c") == C
    assert language("a.cpp") == CXX
    assert language("a.cc") == CXX
