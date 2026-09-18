from modules.file_forensics.engine import locking_processes as lp


def test_translates_the_path_and_returns_matches(monkeypatch):
    monkeypatch.setattr(lp, "to_device_path",
                        lambda p: r"\Device\HarddiskVolume3\Users\me\f.txt")

    class _FakeMatch:
        pid = 1234
        process = "notepad"
        kind = "Handle"
        type_name = "File"
        detail = r"\Device\HarddiskVolume3\Users\me\f.txt"

    class _FakeReport:
        matches = [_FakeMatch()]
        def summary(self):
            return "1 matches in 250 processes"

    monkeypatch.setattr(lp, "find", lambda *a, **k: _FakeReport())

    procs, summary = lp.find_locking_processes(r"C:\Users\me\f.txt")

    assert len(procs) == 1
    assert procs[0].pid == 1234
    assert procs[0].process == "notepad"
    assert "1 matches" in summary


def test_a_path_that_cannot_be_translated_still_returns_a_report(monkeypatch):
    monkeypatch.setattr(lp, "to_device_path", lambda p: None)
    procs, summary = lp.find_locking_processes(r"\\server\share\f.txt")
    assert procs == []
    assert "could not be translated" in summary.lower()


def test_only_file_type_matches_are_kept(monkeypatch):
    monkeypatch.setattr(lp, "to_device_path", lambda p: r"\Device\HarddiskVolume3\f.txt")

    class _RegMatch:
        pid = 1
        process = "svchost"
        kind = "Handle"
        type_name = "Key"
        detail = "HKLM\\Software"

    class _FileMatch:
        pid = 2
        process = "notepad"
        kind = "Handle"
        type_name = "File"
        detail = r"\Device\HarddiskVolume3\f.txt"

    class _FakeReport:
        matches = [_RegMatch(), _FileMatch()]
        def summary(self):
            return "ok"

    monkeypatch.setattr(lp, "find", lambda *a, **k: _FakeReport())
    procs, _ = lp.find_locking_processes(r"C:\f.txt")
    assert len(procs) == 1
    assert procs[0].process == "notepad"
