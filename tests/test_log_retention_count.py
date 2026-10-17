"""Session logs are capped by count, not only by age.

101 of them accumulated in the repo root in two days: one file per launch,
and rotation was 30-day only, so a development day never triggered it.
"""
import os
import time

from core.log_rotation import keep_newest, rotate_old_files


def _write(tmp_path, name, age_days=0):
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


def test_keeps_the_newest_and_deletes_the_rest(tmp_path):
    for i in range(10):
        _write(tmp_path, f"VRK_{i}.log", age_days=10 - i)

    deleted = keep_newest(str(tmp_path), "*.log", 3)

    assert deleted == 7
    survivors = sorted(p.name for p in tmp_path.iterdir())
    assert survivors == ["VRK_7.log", "VRK_8.log", "VRK_9.log"]


def test_is_a_no_op_when_there_are_fewer_files_than_the_cap(tmp_path):
    _write(tmp_path, "a.log")
    _write(tmp_path, "b.log")

    assert keep_newest(str(tmp_path), "*.log", 20) == 0
    assert len(list(tmp_path.iterdir())) == 2


def test_zero_keeps_everything(tmp_path):
    for i in range(5):
        _write(tmp_path, f"{i}.log", age_days=i)

    assert keep_newest(str(tmp_path), "*.log", 0) == 0
    assert len(list(tmp_path.iterdir())) == 5


def test_only_touches_the_pattern(tmp_path):
    for i in range(5):
        _write(tmp_path, f"{i}.log", age_days=i)
    _write(tmp_path, "keep-me.txt", age_days=99)

    keep_newest(str(tmp_path), "*.log", 1)

    assert (tmp_path / "keep-me.txt").exists()


def test_a_missing_directory_is_not_an_error(tmp_path):
    assert keep_newest(str(tmp_path / "nope"), "*.log", 5) == 0


def test_the_session_glob_never_matches_other_programs_logs(tmp_path):
    """The session log sits beside the portable exe, in a folder shared with
    other programs; "*.log" there deleted their logs too."""
    from core.logging_service import session_log_glob

    ours = [_write(tmp_path, f"VRK_2026-09-{d:02d}_10-00-00.log", age_days=60) for d in range(1, 4)]
    theirs = [_write(tmp_path, n, age_days=60) for n in
              ("install.log", "OTHER_2026-09-01_10-00-00.log", "VRK_notes.log",
               "VRK_2026-09-01_10-00-00.log.bak")]

    rotate_old_files(str(tmp_path), session_log_glob("VRK"), 30)
    keep_newest(str(tmp_path), session_log_glob("VRK"), 0)

    assert not any(p.exists() for p in ours)
    assert all(p.exists() for p in theirs)


def test_the_two_rules_compose(tmp_path):
    """Old AND within the newest N still goes: either rule may delete."""
    for i in range(30):
        _write(tmp_path, f"{i:02d}.log", age_days=40)

    rotate_old_files(str(tmp_path), "*.log", 30)

    assert list(tmp_path.iterdir()) == []
