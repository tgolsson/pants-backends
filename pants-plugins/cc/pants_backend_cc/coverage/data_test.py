from __future__ import annotations

from pants_backend_cc.coverage.data import gcda_destinations


def test_gcda_lands_next_to_the_notes_of_the_longest_suffix() -> None:
    notes = ["build/CMakeFiles/lib.dir/a.c.gcno", "CMakeFiles/lib.dir/a.c.gcno", "toybuild/b.gcno"]
    written = [
        "__pants_gcda/tmp/pants-sandbox-Ab12/build/CMakeFiles/lib.dir/a.c.gcda",
        "__pants_gcda/var/tmp/deeper/pants-sandbox-Cd34/toybuild/b.gcda",
    ]
    assert gcda_destinations(written, notes) == {
        written[0]: "build/CMakeFiles/lib.dir/a.c.gcda",
        written[1]: "toybuild/b.gcda",
    }


def test_gcda_without_notes_is_left_out() -> None:
    assert gcda_destinations(["__pants_gcda/tmp/pants-sandbox-Ab12/x/c.gcda"], ["x/d.gcno"]) == {}
