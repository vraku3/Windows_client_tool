from modules.file_forensics import history_log


def test_record_then_recent_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(history_log, "_history_path",
                        lambda: str(tmp_path / "history.json"))

    history_log.record({"path": r"C:\a.txt", "creator": "installer.exe"})
    history_log.record({"path": r"C:\b.txt", "creator": "chrome.exe"})

    entries = history_log.recent(limit=10)
    assert len(entries) == 2
    assert entries[0]["path"] == r"C:\b.txt"  # newest first


def test_search_filters_by_any_field_substring(tmp_path, monkeypatch):
    monkeypatch.setattr(history_log, "_history_path",
                        lambda: str(tmp_path / "history.json"))
    history_log.record({"path": r"C:\setup_123.tmp", "creator": "installer.exe"})
    history_log.record({"path": r"C:\report.docx", "creator": "winword.exe"})

    results = history_log.search("installer")
    assert len(results) == 1
    assert results[0]["creator"] == "installer.exe"


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(history_log, "_history_path",
                        lambda: str(tmp_path / "history.json"))
    monkeypatch.setattr(history_log, "_CAP", 5)
    for i in range(10):
        history_log.record({"path": f"C:\\{i}.txt", "creator": "x"})

    assert len(history_log.recent(limit=100)) == 5
