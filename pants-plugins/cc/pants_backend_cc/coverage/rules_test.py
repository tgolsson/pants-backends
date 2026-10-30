from __future__ import annotations

import pytest

from pants_backend_cc.coverage.rules import agreeing_groups, below_threshold


def test_tests_that_agree_share_a_group() -> None:
    files = [
        {"lib.gcno": "x", "src/a.c": "a", "t1.gcno": "1"},
        {"lib.gcno": "x", "src/a.c": "a", "t2.gcno": "2"},
        # Another configure of the same build directory.
        {"lib.gcno": "y", "src/a.c": "a", "CMakeCache.txt": "B", "t3.gcno": "3"},
        {"lib.gcno": "x", "CMakeCache.txt": "A"},
        {"lib.gcno": "y", "CMakeCache.txt": "B"},
    ]
    assert agreeing_groups(files) == [[0, 1, 3], [2, 4]]


@pytest.mark.parametrize(
    "exit_code, below",
    [
        (0, False),
        (2, True),
        (4, True),
        (2 | 16, True),
        (2 | 4 | 8 | 16, True),
        (1, False),
        (64, False),
        (64 | 2, False),
        (128, False),
    ],
)
def test_below_threshold(exit_code: int, below: bool) -> None:
    assert below_threshold(exit_code) is below
