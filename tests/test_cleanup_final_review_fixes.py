"""Pins for the final whole-branch review of the Cleanup deletion engine
(2026-10-11): each test names the finding it closes."""
import json
import logging
import os
import time
import types

import pytest

from modules.cleanup import cleanup_history as ch
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss
from modules.cleanup.cleanup_scanner.deleter import DeleteReport


def _file(path, size, age_s=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_s:
        t = time.time() - age_s
        os.utime(path, (t, t))
    return path


@pytest.fixture
def no_system_guards(monkeypatch):
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])


def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


# ── 3. Resolved-path refusal ──────────────────────────────────────────────

def test_a_junction_to_a_protected_folder_is_refused(tmp_path, monkeypatch):
    protected = tmp_path / "Package Cache"
    _file(protected / "setup.exe", 5)
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [str(protected)])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    link = tmp_path / "innocent"
    _junction(link, protected)
    reason = ss._refusal_reason(ScanItem(str(link), 5, True))
    assert reason == "uninstall and repair of installed software depend on it"


def test_a_path_reached_through_a_junction_into_a_sign_in_store_is_refused(tmp_path, monkeypatch):
    store = tmp_path / "TokenBroker"
    _file(store / "Cache" / "t.bin", 5)
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    monkeypatch.setattr(ss, "sign_in_stores", lambda: [str(store)])
    link = tmp_path / "cache-root"
    _junction(link, store)
    reason = ss._refusal_reason(ScanItem(str(link / "Cache"), 5, True))
    assert reason == "it is, or holds, a sign-in token store"


def test_a_junction_to_a_folder_HOLDING_protected_data_is_refused(tmp_path, monkeypatch):
    outer = tmp_path / "Local"
    outlook = outer / "Microsoft" / "Outlook"
    outlook.mkdir(parents=True)
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_user_data_dirs", lambda: [str(outlook)])
    link = tmp_path / "looks-like-a-cache"
    _junction(link, outer)
    assert ss._refusal_reason(ScanItem(str(link), 0, True)) is not None


def test_an_ordinary_folder_is_still_not_refused(tmp_path, no_system_guards):
    _file(tmp_path / "junk.tmp", 5)
    assert ss._refusal_reason(ScanItem(str(tmp_path), 5, True)) is None


# ── 4. Export ─────────────────────────────────────────────────────────────

def test_delete_items_report_is_exported_through_the_facade():
    assert "delete_items_report" in ss.__all__
    assert cs.delete_items_report is ss.delete_items_report


def _fake_stage_scanners(monkeypatch, items):
    import modules.cleanup.cleanup_scanner as pkg
    from modules.cleanup.cleanup_scanner import catalog, scan_cache
    monkeypatch.setattr(catalog, "scanners_for", lambda *a, **k: {})
    scan_cache.invalidate()

    def scan(min_age_days=0):
        return cs.ScanResult(items, sum(i.size for i in items))
    monkeypatch.setattr(pkg, "_OV_GROUPS", [("Fake", [scan]), ("Browser Caches", None)])


def test_run_cleanup_safe_stage_runs_end_to_end_and_reports_measured_bytes(tmp_path, monkeypatch, no_system_guards):
    from modules.updates import stage_runners
    _file(tmp_path / "a.tmp", 100, age_s=3 * 86400)
    held = _file(tmp_path / "held.tmp", 900, age_s=3 * 86400)
    # Scanned size says 1000; only the 100 bytes that really went may be reported.
    _fake_stage_scanners(monkeypatch, [ScanItem(str(tmp_path), 1000, True, selected=False)])
    lines = []
    app = types.SimpleNamespace(config=None)
    fd = os.open(str(held), os.O_RDONLY)
    try:
        out = stage_runners.run_cleanup_safe_stage(app, lines.append, lambda: False)
    finally:
        os.close(fd)
    assert out["freed"] == 100
    assert any("freed" in line for line in lines)


# ── 5. Cancellation reaches the engine ────────────────────────────────────

def test_stage_runner_cancellation_reaches_the_engine(tmp_path, monkeypatch, no_system_guards):
    from modules.updates import stage_runners
    for i in range(5):
        _file(tmp_path / f"{i}.tmp", 10, age_s=3 * 86400)
    _fake_stage_scanners(monkeypatch, [ScanItem(str(tmp_path), 50, True)])
    calls = {"n": 0}

    def is_cancelled():
        # Not cancelled while scanning; cancelled once the delete has started.
        # 1 scan check, 1 per-item check, 1 delete_path entry, 1 for the
        # first file -- then cancelled.
        calls["n"] += 1
        return calls["n"] > 4
    out = stage_runners.run_cleanup_safe_stage(types.SimpleNamespace(config=None),
                                               lambda _m: None, is_cancelled)
    entry = ch.recent_deleted(1)[0]
    assert entry["cancelled"] is True
    assert 0 < out["freed"] == entry["bytes"] < 50


def test_a_cancelled_run_records_its_partial_measured_result(tmp_path, no_system_guards):
    for i in range(4):
        _file(tmp_path / f"{i}.tmp", 10, age_s=3 * 86400)
    seen = {"n": 0}

    def cancelled():
        seen["n"] += 1
        return seen["n"] > 3          # item check, then two files, then stop
    report, _refused, _per = ss.delete_items_report([ScanItem(str(tmp_path), 40, True)],
                                                    cancelled=cancelled)
    assert report.cancelled and 0 < report.freed < 40
    entry = ch.recent_deleted(1)[0]
    assert entry["cancelled"] is True and entry["bytes"] == report.freed


# ── 6. Top-level unreadable items ─────────────────────────────────────────

def test_an_unreadable_top_level_item_is_denied_and_a_problem(tmp_path, no_system_guards, monkeypatch, caplog):
    target = _file(tmp_path / "x.tmp", 5)
    real_lstat = os.lstat

    def lstat(path, *a, **k):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(target)):
            raise PermissionError(5, "Access is denied", str(target))
        return real_lstat(path, *a, **k)
    monkeypatch.setattr(ss.os, "lstat", lstat)
    with caplog.at_level(logging.WARNING):
        report, refused, _per = ss.delete_items_report([ScanItem(str(target), 5, False)])
    assert report.denied == [str(target)] and refused == []
    assert any(str(target) in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert ss.delete_items([ScanItem(str(target), 5, False)]) == (0, 1)
    assert target.exists()


def test_an_absent_top_level_item_is_skipped_silently(tmp_path, no_system_guards):
    report, refused, per_item = ss.delete_items_report([ScanItem(str(tmp_path / "gone"), 5, False)])
    assert report.denied == [] and refused == [] and per_item == []


# ── 7. Robust reads ───────────────────────────────────────────────────────

BAD_LOGS = [b"\xff\xfe\x00bad", b"{}", b"null", b'[1, "x", null, {"bytes": 5, "count": 1}]']


@pytest.mark.parametrize("payload", BAD_LOGS)
def test_recent_deleted_survives_a_malformed_log(tmp_path, monkeypatch, payload):
    log = tmp_path / "log.json"
    log.write_bytes(payload)
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(log))
    got = ch.recent_deleted()
    assert all(isinstance(e, dict) for e in got)


@pytest.mark.parametrize("payload", BAD_LOGS)
def test_the_run_log_readers_survive_a_malformed_log(tmp_path, monkeypatch, payload):
    log = tmp_path / "runs.json"
    log.write_bytes(payload)
    monkeypatch.setattr(ch, "_history_path", lambda: str(log))
    assert all(isinstance(e, dict) for e in ch.recent())
    assert isinstance(ch.total_freed_all_time(), int)
    ch.record(10)
    assert json.loads(log.read_text())[-1]["freed_bytes"] == 10


def test_a_non_list_log_warns(tmp_path, monkeypatch, caplog):
    log = tmp_path / "log.json"
    log.write_bytes(b"{}")
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(log))
    with caplog.at_level(logging.WARNING):
        assert ch.recent_deleted() == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("payload", BAD_LOGS)
def test_history_tab_and_summary_bar_do_not_raise_on_a_malformed_log(qapp, tmp_path, monkeypatch, payload):
    from modules.cleanup.components.drive_summary_bar import DriveSummaryBar
    from modules.cleanup.tabs._history_tab import _HistoryTab
    log = tmp_path / "log.json"
    log.write_bytes(payload)
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(log))
    tab = _HistoryTab()
    tab.refresh()
    bar = DriveSummaryBar()
    bar._refresh_last()
    assert bar.last.text()


# ── 8. Removals without bytes ─────────────────────────────────────────────

def test_an_empty_folder_removed_is_logged_with_zero_bytes(tmp_path, no_system_guards):
    empty = tmp_path / "empty"
    empty.mkdir()
    report, _r, _p = ss.delete_items_report([ScanItem(str(empty), 0, True, keep_root=False)])
    assert not empty.exists() and report.files == 0 and report.freed == 0
    entry = ch.recent_deleted(1)[0]
    assert entry["largest"] == [[str(empty), 0]]


def test_record_report_writes_a_removal_without_files_or_bytes(tmp_path):
    ch.record_report(DeleteReport(), [], removed=[(r"C:\x\link", 0)])
    assert ch.recent_deleted(1)[0]["largest"] == [[r"C:\x\link", 0]]


def test_record_report_writes_a_cancelled_run_that_freed_nothing():
    ch.record_report(DeleteReport(cancelled=True), [])
    assert ch.recent_deleted(1)[0]["cancelled"] is True
