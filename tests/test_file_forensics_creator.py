from datetime import datetime, timedelta, timezone

from modules.file_forensics.engine import creator_heuristic as ch


class _FakeProc:
    def __init__(self, pid, name, create_time_filetime):
        self.pid = pid
        self.name = name
        self.create_time = create_time_filetime


def _filetime_for(dt: datetime) -> int:
    """Inverse of ch._filetime_to_datetime, for building test fixtures.

    ch._filetime_to_datetime returns LOCAL wall-clock time (it builds the
    UTC instant, then `.astimezone()`s to local, then drops tzinfo) -- so
    to invert it, `dt` (naive) must be treated as a LOCAL time and
    converted to the aware UTC instant before measuring its distance from
    the FILETIME epoch. `datetime.astimezone()` on a naive datetime
    presumes it represents system-local time, which is exactly this.
    """
    utc = dt.astimezone(timezone.utc)
    delta = utc - ch._FILETIME_EPOCH
    return int(delta.total_seconds() * 10_000_000)


def test_finds_a_process_started_close_to_file_creation(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    close_proc = _FakeProc(111, "installer.exe", _filetime_for(created + timedelta(seconds=2)))
    far_proc = _FakeProc(222, "explorer.exe", _filetime_for(created - timedelta(minutes=10)))

    monkeypatch.setattr(ch, "system_processes", lambda: [close_proc, far_proc])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": f"C:\\{pid}.exe", "user": "TESTUSER",
                  "path_error": None, "user_error": None})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert len(candidates) == 1
    assert candidates[0].pid == 111
    assert candidates[0].name == "installer.exe"
    assert abs(candidates[0].delta_seconds - 2.0) < 0.01
    assert candidates[0].path_error is None
    assert candidates[0].user_error is None


def test_refused_path_and_user_reads_are_not_collapsed_to_none(monkeypatch):
    """A refused read (access denied) must stay distinguishable from a
    process that genuinely has no path/user -- see
    core.procengine.details.ProcessDetails's own docstring on this."""
    created = datetime(2026, 1, 1, 12, 0, 0)
    proc = _FakeProc(111, "protected.exe", _filetime_for(created + timedelta(seconds=1)))

    monkeypatch.setattr(ch, "system_processes", lambda: [proc])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None,
                  "path_error": "Access is denied.",
                  "user_error": "Access is denied."})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert len(candidates) == 1
    assert candidates[0].path is None
    assert candidates[0].path_error == "Access is denied."
    assert candidates[0].user is None
    assert candidates[0].user_error == "Access is denied."


def test_multiple_candidates_are_sorted_by_closeness(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    a = _FakeProc(1, "a.exe", _filetime_for(created + timedelta(seconds=4)))
    b = _FakeProc(2, "b.exe", _filetime_for(created + timedelta(seconds=1)))

    monkeypatch.setattr(ch, "system_processes", lambda: [a, b])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None,
                  "path_error": None, "user_error": None})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert [c.pid for c in candidates] == [2, 1]


def test_no_candidates_within_tolerance_returns_empty(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    far = _FakeProc(1, "far.exe", _filetime_for(created - timedelta(hours=1)))
    monkeypatch.setattr(ch, "system_processes", lambda: [far])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None})())

    assert ch.find_creator_candidates(created, tolerance_seconds=5.0) == []
