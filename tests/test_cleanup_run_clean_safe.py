"""run_clean_safe consolidates what used to be three separate, near-
identical confirm/worker/delete/combine implementations (_OverviewTab,
QuickCleanupTab, _ScanTab all had their own).
"""
import time

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QWidget

from modules.cleanup import cleanup_scanner as cs
from modules.cleanup import clean_safe_runner as csr


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def widget(qapp):
    w = QWidget()
    yield w


def test_always_confirm_runs_the_delete_when_accepted(qapp, widget, monkeypatch):
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (2, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    csr.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert done == [(2, 0)]


def test_always_confirm_does_nothing_when_cancelled(qapp, widget, monkeypatch):
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Cancel)
    called = []
    monkeypatch.setattr(cs, "delete_items", lambda *a, **k: called.append(1))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    result = csr.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: None)

    assert result is None
    assert called == []


def test_size_gated_confirm_skips_the_dialog_for_small_totals(qapp, widget, monkeypatch):
    # CONFIRM_BYTES threshold lives in _scan_tab.py; a single 100-byte item
    # is always under it, so no dialog should even be constructed.
    def _fail_if_called(self):
        raise AssertionError("confirm dialog should not have been shown")
    monkeypatch.setattr(csr.QMessageBox, "exec", _fail_if_called)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    csr.run_clean_safe(widget, [item], confirm="size_gated", on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert done == [(1, 0)]


def test_only_selected_items_count_toward_the_confirmed_total(qapp, widget, monkeypatch):
    # _ScanTab passes its FULL item list (mixed selected/unselected) and
    # relies on filtering happening downstream, same as cs.delete_items
    # itself does -- an unselected item must not inflate the confirm total.
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))
    seen_totals = []

    def _fake_exec(self):
        seen_totals.append(self.text())
        return csr.QMessageBox.StandardButton.Ok
    monkeypatch.setattr(csr.QMessageBox, "exec", _fake_exec)

    selected_item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe", selected=True)
    unselected_item = cs.ScanItem(path=r"C:\y", size=999_000_000, is_dir=False, safety="safe", selected=False)
    done = []
    csr.run_clean_safe(widget, [selected_item, unselected_item], confirm="always",
                       on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert len(seen_totals) == 1
    assert cs.format_size(100) in seen_totals[0]
    assert cs.format_size(999_000_000) not in seen_totals[0]


def test_browser_cats_are_combined_with_regular_items(qapp, widget, monkeypatch):
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))
    monkeypatch.setattr("modules.cleanup.browser_scanner.delete_selected", lambda cats, progress_cb=None: (1, 1))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    csr.run_clean_safe(widget, [item], browser_cats=["fake_cat"], confirm="always",
                       on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert done == [(2, 1)]  # 1 (regular) + 1 (browser) deleted, 0 + 1 errors


def test_on_error_fires_when_the_worker_raises(qapp, widget, monkeypatch):
    def _raise(items, stop_wuauserv=False):
        raise RuntimeError("disk went away")
    monkeypatch.setattr(cs, "delete_items", _raise)
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Ok)

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    errors = []
    csr.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: None,
                       on_error=errors.append)
    _settle(qapp)

    assert len(errors) == 1
    assert "disk went away" in errors[0]
