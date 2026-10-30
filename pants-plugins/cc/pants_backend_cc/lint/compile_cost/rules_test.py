import pytest

from pants_backend_cc.lint.compile_cost.rules import (
    Phases,
    Row,
    lower,
    parse_time_report,
    render,
    summary,
)

# g++ 13.3 on a C++ TU instantiating templates, and gcc 13.3 on a C TU, which defers nothing.
CXX_REPORT = """
Time variable                                   usr           sys          wall           GGC
 |name lookup                       :   0.00 (  0%)   0.00 (  0%)   0.07 ( 26%)  1339k (  5%)
 phase setup                        :   0.00 (  0%)   0.00 (  0%)   0.00 (  0%)  1882k (  7%)
 phase parsing                      :   0.11 ( 79%)   0.10 ( 91%)   0.22 ( 85%)    23M ( 83%)
 phase lang. deferred               :   0.02 (  0%)   0.01 (  9%)   0.00 (  0%)  1174k (  4%)
 phase opt and generate             :   0.03 ( 21%)   0.00 (  0%)   0.04 ( 15%)  1839k (  6%)
 TOTAL                              :   0.16          0.11          0.26           28M
"""

C_REPORT = """
Time variable                                   usr           sys          wall           GGC
 phase setup                        :   0.01 (  0%)   0.00 (  0%)   0.00 (  0%)  1819k ( 49%)
 phase parsing                      :   0.01 (100%)   0.01 (100%)   0.03 ( 75%)  1782k ( 48%)
 phase opt and generate             :   0.02 (  0%)   0.01 (  0%)   0.01 ( 25%)   136k (  4%)
 TOTAL                              :   0.04          0.02          0.04         3738k
"""

CLANG_REPORT = """
===-------------------------------------------------------------------------===
                      ... Pass execution timing report ...
===-------------------------------------------------------------------------===
  Total Execution Time: 0.0055 seconds (0.0091 wall clock)
"""


def test_frontend_is_parsing_and_deferred_work_in_user_time():
    phases = parse_time_report(CXX_REPORT)
    assert phases == Phases(
        frontend=pytest.approx(0.13),
        frontend_sys=pytest.approx(0.11),
        backend=pytest.approx(0.03),
        total=pytest.approx(0.16),
    )


def test_without_deferred_work():
    phases = parse_time_report(C_REPORT)
    assert phases == Phases(
        frontend=pytest.approx(0.01),
        frontend_sys=pytest.approx(0.01),
        backend=pytest.approx(0.03),
        total=pytest.approx(0.04),
    )


def test_clang_has_no_phases():
    assert parse_time_report(CLANG_REPORT) is None


def test_the_lower_frontend_wins_and_a_tie_keeps_the_first():
    first = Phases(0.5, 0.1, 0.2, 0.7)
    assert lower(first, Phases(0.4, 0.9, 0.9, 1.3)) == Phases(0.4, 0.9, 0.9, 1.3)
    assert lower(first, Phases(0.6, 0.0, 0.0, 0.6)) is first
    assert lower(first, Phases(0.5, 0.0, 0.0, 0.5)) is first


ROWS = [
    Row("src/fast.cpp", Phases(0.1, 0.2, 0.5, 0.6), gated=True),
    Row("src/kernels/k.cpp", Phases(0.9, 0.0, 0.1, 1.0), gated=False),
    Row("src/slow.cpp", Phases(0.4, 0.3, 0.1, 0.5), gated=True),
    Row("src/slower.cpp", Phases(0.6, 0.0, 0.1, 0.7), gated=True),
]


def test_render_sorts_by_frontend_and_marks_the_gate():
    table = render(ROWS, 0.3, ["src/kernels"]).splitlines()
    assert table[2] == (
        "gcc `-ftime-report` user CPU time per compile; the frontend's system time is shown apart and "
        "not gated. 2 of 3 gated TUs over the 0.3 s frontend budget; TUs under `src/kernels` are "
        "reported, not gated."
    )
    assert table[4] == "| TU | frontend (s) | frontend sys (s) | backend (s) | total (s) | budget |"
    assert table[6:] == [
        "| src/kernels/k.cpp | 0.900 | 0.000 | 0.100 | 1.000 | not gated |",
        "| src/slower.cpp | 0.600 | 0.000 | 0.100 | 0.700 | **over** |",
        "| src/slow.cpp | 0.400 | 0.300 | 0.100 | 0.500 | **over** |",
        "| src/fast.cpp | 0.100 | 0.200 | 0.500 | 0.600 | ok |",
    ]
    unset = render(ROWS, None, []).splitlines()
    assert unset[2].endswith(" 3 gated TUs, no frontend budget set.")
    assert unset[8] == "| src/slow.cpp | 0.400 | 0.300 | 0.100 | 0.500 | no budget |"


def test_the_summary_names_the_tus_over_budget():
    assert summary(ROWS, 0.3) == (
        "compile-cost: 2 of 3 gated TUs over the 0.3 s frontend budget\n"
        "  src/slower.cpp: 0.600 s\n"
        "  src/slow.cpp: 0.400 s\n"
    )
    assert summary(ROWS, None) == "compile-cost: 3 gated TUs, no frontend budget set\n"
