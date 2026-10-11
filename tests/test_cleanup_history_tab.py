from modules.cleanup import cleanup_history as ch


def test_history_tab_lists_entries_newest_first_with_the_measured_summary(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    ch.record_report(DeleteReport(freed=1024 ** 3, files=10), [])
    ch.record_report(DeleteReport(freed=5, files=1, locked=[r"C:\T\a.log"]),
                     [(r"C:\P\LocalCache", "holds an app's sign-in")], owners={"Teams.exe": 1})
    from modules.cleanup.tabs._history_tab import _HistoryTab
    tab = _HistoryTab()
    tab.refresh()
    assert tab.table.rowCount() == 2
    assert tab.table.item(0, 3).text() == "1"            # newest first: 1 left in use
    tab.table.selectRow(0)
    detail = tab.detail.toPlainText()
    assert r"C:\T\a.log" in detail and "Teams.exe" in detail
    assert "holds an app's sign-in" in detail


def test_an_empty_log_says_so(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "none.json"))
    from modules.cleanup.tabs._history_tab import _HistoryTab
    tab = _HistoryTab()
    tab.refresh()
    assert tab.table.rowCount() == 0 and "No cleans recorded yet" in tab.status.text()


def test_detail_lists_largest_removed_and_cloud_only(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    import json
    (tmp_path / "log.json").write_text(json.dumps([{
        "at": "2026-10-11T10:00:00", "count": 2, "bytes": 3000, "locked_count": 0,
        "largest": [[r"C:\Temp\big.tmp", 2048]], "cloud_only": 4}]), encoding="utf-8")
    from modules.cleanup.tabs._history_tab import _HistoryTab, _path_of
    tab = _HistoryTab()
    tab.refresh()
    tab.table.selectRow(0)
    detail = tab.detail.toPlainText()
    assert "Removed (largest):" in detail and r"C:\Temp\big.tmp" in detail
    assert "Cloud-only files left: 4" in detail
    line = [ln for ln in detail.splitlines() if "big.tmp" in ln][0]
    assert _path_of(line) == r"C:\Temp\big.tmp"


def _tab_with_path(tmp_path, monkeypatch, path):
    import json
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    (tmp_path / "log.json").write_text(json.dumps([{
        "at": "2026-10-11T10:00:00", "count": 1, "bytes": 5, "locked_count": 1,
        "locked": [path]}]), encoding="utf-8")
    from modules.cleanup.tabs._history_tab import _HistoryTab
    tab = _HistoryTab()
    tab.refresh()
    tab.table.selectRow(0)
    lines = tab.detail.toPlainText().splitlines()
    cur = tab.detail.textCursor()
    cur.setPosition(tab.detail.toPlainText().index(path))
    tab.detail.setTextCursor(cur)
    return tab


def test_open_folder_walks_up_to_the_nearest_existing_ancestor(qapp, tmp_path, monkeypatch):
    import subprocess
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda a, *k, **kw: launched.append(a))
    tab = _tab_with_path(tmp_path, monkeypatch, str(tmp_path / "gone" / "deeper" / "x.log"))
    tab._open_folder()
    assert launched == [["explorer", str(tmp_path)]]


def test_open_folder_with_no_existing_ancestor_launches_nothing(qapp, tmp_path, monkeypatch):
    import subprocess
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda a, *k, **kw: launched.append(a))
    tab = _tab_with_path(tmp_path, monkeypatch, "Q:" + chr(92) + "nope" + chr(92) + "x")
    tab._open_folder()
    assert launched == [] and "no longer exists" in tab.status.text()


def _wait_for(cond, timeout=5.0):
    import time
    from PyQt6.QtWidgets import QApplication
    end = time.time() + timeout
    while not cond() and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert cond()


def test_summary_bar_shows_every_fixed_drive_and_the_last_clean(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    ch.record_report(DeleteReport(freed=3 * 1024 ** 3, files=7), [])
    from modules.cleanup.components import drive_summary_bar as dsb
    monkeypatch.setattr(dsb, "drive_lines", lambda: [("C:\\", 400 * 1024 ** 3, 1000 * 1024 ** 3),
                                                     ("E:\\", 10 * 1024 ** 3, 100 * 1024 ** 3)])
    bar = dsb.DriveSummaryBar()
    bar.refresh()
    _wait_for(lambda: len(bar.bars) == 2)
    texts = [b.format() for b in bar.bars]
    assert texts[0].startswith("C:") and "free of" in texts[0] and len(texts) == 2
    assert bar.bars[1].value() == 90                       # E: is 90 % used
    assert bar.last.text().startswith("Last clean: Freed 3.0 GB")


def test_summary_bar_ignores_a_stale_generation(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.components import drive_summary_bar as dsb
    monkeypatch.setattr(dsb, "drive_lines", lambda: [])
    bar = dsb.DriveSummaryBar()
    bar.refresh()
    old = bar._generation
    bar.refresh()
    bar._deliver(old, [("C:\\", 1, 2)])
    assert bar.bars == []
    bar._deliver(bar._generation, [("C:\\", 1, 2)])
    assert len(bar.bars) == 1
    bar.cancel()


def test_summary_bar_refresh_reads_drives_off_the_calling_thread(qapp, tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.components import drive_summary_bar as dsb
    seen = []

    def fake():
        seen.append(threading.get_ident())
        return [("C:\\", 1, 2)]

    monkeypatch.setattr(dsb, "drive_lines", fake)
    bar = dsb.DriveSummaryBar()
    bar.refresh()
    _wait_for(lambda: len(bar.bars) == 1)
    assert seen and seen[0] != threading.get_ident()
