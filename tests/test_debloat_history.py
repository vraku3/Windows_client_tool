from modules.debloat import debloat_history as dh


def test_record_and_read_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "debloat_history.json"
    monkeypatch.setattr(dh, "_history_path", lambda: str(path))
    dh.record("Apps", success=3, total=4)
    entries = dh.recent(limit=20)
    assert len(entries) == 1
    assert entries[0]["kind"] == "Apps" and entries[0]["success"] == 3


def test_recent_returns_newest_first(tmp_path, monkeypatch):
    path = tmp_path / "debloat_history.json"
    monkeypatch.setattr(dh, "_history_path", lambda: str(path))
    dh.record("Apps", success=1, total=1)
    dh.record("Tweaks", success=2, total=2)
    entries = dh.recent(limit=20)
    assert [e["kind"] for e in entries] == ["Tweaks", "Apps"]
