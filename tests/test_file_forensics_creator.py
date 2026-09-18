from datetime import datetime, timedelta

from modules.file_forensics.engine import creator_heuristic as ch


class _FakeProc:
    def __init__(self, pid, name, create_time_filetime):
        self.pid = pid
        self.name = name
        self.create_time = create_time_filetime


def _filetime_for(dt: datetime) -> int:
    """Inverse of ch._filetime_to_datetime, for building test fixtures."""
    delta = dt - ch._FILETIME_EPOCH.replace(tzinfo=None)
    return int(delta.total_seconds() * 10_000_000)


def test_finds_a_process_started_close_to_file_creation(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    close_proc = _FakeProc(111, "installer.exe", _filetime_for(created + timedelta(seconds=2)))
    far_proc = _FakeProc(222, "explorer.exe", _filetime_for(created - timedelta(minutes=10)))

    monkeypatch.setattr(ch, "system_processes", lambda: [close_proc, far_proc])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": f"C:\\{pid}.exe", "user": "TESTUSER"})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert len(candidates) == 1
    assert candidates[0].pid == 111
    assert candidates[0].name == "installer.exe"
    assert abs(candidates[0].delta_seconds - 2.0) < 0.01


def test_multiple_candidates_are_sorted_by_closeness(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    a = _FakeProc(1, "a.exe", _filetime_for(created + timedelta(seconds=4)))
    b = _FakeProc(2, "b.exe", _filetime_for(created + timedelta(seconds=1)))

    monkeypatch.setattr(ch, "system_processes", lambda: [a, b])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert [c.pid for c in candidates] == [2, 1]


def test_no_candidates_within_tolerance_returns_empty(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    far = _FakeProc(1, "far.exe", _filetime_for(created - timedelta(hours=1)))
    monkeypatch.setattr(ch, "system_processes", lambda: [far])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None})())

    assert ch.find_creator_candidates(created, tolerance_seconds=5.0) == []
