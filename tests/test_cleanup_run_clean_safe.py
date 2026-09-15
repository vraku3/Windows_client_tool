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


def test_a_result_landing_after_the_widget_is_deleted_is_dropped(qapp, widget, monkeypatch):
    """run_clean_safe connects CLOSURES to the worker's signals, not bound
    methods, so Qt cannot auto-disconnect them when `widget` is destroyed --
    unlike the bound-method connection _ScanTab used before this helper
    consolidated three near-identical implementations into one. Mirrors
    tests/test_cleanup_late_signal.py's pattern for the same shape of bug
    elsewhere in this module."""
    import threading

    from PyQt6 import sip

    started = threading.Event()
    release = threading.Event()

    def _blocking_delete(items, stop_wuauserv=False):
        started.set()
        release.wait(10)
        return (1, 0)

    monkeypatch.setattr(cs, "delete_items", _blocking_delete)
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Ok)

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    csr.run_clean_safe(widget, [item], confirm="always",
                       on_done=lambda d, e: done.append((d, e)))

    assert started.wait(5), "delete_items was never called"
    sip.delete(widget)
    release.set()
    _settle(qapp)

    assert done == [], "on_done fired for a result delivered after the widget was deleted"


def test_an_error_landing_after_the_widget_is_deleted_is_dropped(qapp, widget, monkeypatch):
    import threading

    from PyQt6 import sip

    started = threading.Event()
    release = threading.Event()

    def _blocking_raise(items, stop_wuauserv=False):
        started.set()
        release.wait(10)
        raise RuntimeError("disk went away")

    monkeypatch.setattr(cs, "delete_items", _blocking_raise)
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Ok)

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    errors = []
    csr.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: None,
                       on_error=errors.append)

    assert started.wait(5), "delete_items was never called"
    sip.delete(widget)
    release.set()
    _settle(qapp)

    assert errors == [], "on_error fired for an error delivered after the widget was deleted"


def test_no_preset_label_produces_the_original_message_byte_for_byte(qapp, widget, monkeypatch):
    """Regression pin: confirm="always" with no preset_label (the default,
    and what _scan_tab.py's own confirm="size_gated" path never even
    passes) must render EXACTLY the message this dialog has always shown.
    """
    seen_texts = []

    def _fake_exec(self):
        seen_texts.append(self.text())
        return csr.QMessageBox.StandardButton.Ok
    monkeypatch.setattr(csr.QMessageBox, "exec", _fake_exec)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    csr.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: None)
    _settle(qapp)

    assert seen_texts == [
        f"Clean <b>{cs.format_size(100)}</b> of safe items across "
        f"1 item(s)?<br>This cannot be undone."
    ]


def test_light_preset_label_also_produces_the_original_message(qapp, widget, monkeypatch):
    seen_texts = []

    def _fake_exec(self):
        seen_texts.append(self.text())
        return csr.QMessageBox.StandardButton.Ok
    monkeypatch.setattr(csr.QMessageBox, "exec", _fake_exec)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    csr.run_clean_safe(widget, [item], confirm="always", preset_label="Light",
                        on_done=lambda d, e: None)
    _settle(qapp)

    assert seen_texts == [
        f"Clean <b>{cs.format_size(100)}</b> of safe items across "
        f"1 item(s)?<br>This cannot be undone."
    ]


def test_aggressive_preset_label_drops_the_word_safe_and_names_the_preset(qapp, widget, monkeypatch):
    seen_texts = []

    def _fake_exec(self):
        seen_texts.append(self.text())
        return csr.QMessageBox.StandardButton.Ok
    monkeypatch.setattr(csr.QMessageBox, "exec", _fake_exec)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="danger")
    item.selected = True
    csr.run_clean_safe(widget, [item], confirm="always", preset_label="Aggressive",
                        on_done=lambda d, e: None)
    _settle(qapp)

    assert len(seen_texts) == 1
    assert "safe" not in seen_texts[0].lower()
    assert "Aggressive" in seen_texts[0]


def test_size_gated_confirm_is_unaffected_by_preset_label(qapp, widget, monkeypatch):
    """_scan_tab.py's own call never passes preset_label at all, but even
    if it were passed, size_gated must not use it -- that branch has no
    concept of presets."""
    seen_texts = []

    def _fake_exec(self):
        seen_texts.append(self.text())
        return csr.QMessageBox.StandardButton.Ok
    monkeypatch.setattr(csr.QMessageBox, "exec", _fake_exec)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))

    big_item = cs.ScanItem(path=r"C:\x", size=600 * 1024 * 1024, is_dir=False,
                            safety="safe", selected=True)
    csr.run_clean_safe(widget, [big_item], confirm="size_gated",
                        preset_label="Aggressive", on_done=lambda d, e: None)
    _settle(qapp)

    # _confirm_large's own dialog text, from _scan_tab.py -- unrelated to
    # run_clean_safe's "always" branch and must not mention any preset.
    assert len(seen_texts) == 1
    assert "Aggressive" not in seen_texts[0]
    assert "permanently delete" in seen_texts[0]


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
