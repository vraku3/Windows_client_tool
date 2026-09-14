"""Shared confirm -> worker -> delete -> combine sequence for "Clean All
Safe" actions.

Consolidated out of three near-identical implementations that used to live
one apiece in _OverviewTab, QuickCleanupTab and _ScanTab. Lives outside the
cleanup_scanner package on purpose: that package imports no PyQt6 anywhere
today (the same Qt-free "engine" split this codebase keeps in TreeSize's
scan/+store/, Monitor Control and GPResult), and this helper needs
QMessageBox and QThreadPool.
"""
import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox, QWidget

from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup import cleanup_scanner as cs

logger = logging.getLogger(__name__)


def run_clean_safe(widget: QWidget, items: List["cs.ScanItem"], *,
                   browser_cats: Optional[list] = None,
                   stop_wuauserv: bool = False,
                   confirm: str = "always",
                   on_done: Callable[[int, int], None],
                   on_error: Optional[Callable[[str], None]] = None) -> Optional[Worker]:
    """confirm="always": always asks, via a QMessageBox Ok/Cancel dialog.
    confirm="size_gated": only asks above _scan_tab.CONFIRM_BYTES.

    `items` may be a caller's FULL item list (selected and unselected
    mixed) -- only `.selected` ones count toward the confirmed total and
    toward what actually gets deleted, matching cs.delete_items' own
    filtering (_ScanTab relies on exactly this: it passes every row in
    the tree, not just the checked ones).

    Returns the Worker it started, or None if the user declined the
    confirm dialog -- neither on_done nor on_error fires in that case.
    """
    total = sum(i.size for i in items if i.selected)
    if browser_cats:
        total += sum(getattr(c, "size_bytes", 0) for c in browser_cats)
    item_count = len([i for i in items if i.selected]) + len(browser_cats or [])

    if confirm == "size_gated":
        # Imported lazily: _scan_tab.py imports THIS module at top level,
        # so importing it back here at module load time would be circular.
        from modules.cleanup.tabs._scan_tab import _confirm_large
        if not _confirm_large(widget, total):
            return None
    else:
        mb = QMessageBox(widget)
        mb.setWindowTitle("Confirm Bulk Clean")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            f"Clean <b>{cs.format_size(total)}</b> of safe items across "
            f"{item_count} item(s)?<br>This cannot be undone.")
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return None

    def _run(_worker):
        browser_freed = browser_errors = 0
        if browser_cats:
            from modules.cleanup import browser_scanner as bs
            browser_freed, browser_errors = bs.delete_selected(browser_cats)
        deleted, errors = cs.delete_items(items, stop_wuauserv=stop_wuauserv) if items else (0, 0)
        return deleted + browser_freed, errors + browser_errors

    # These are CLOSURES, not bound methods -- Qt auto-disconnects a bound
    # method when its receiving QObject is destroyed, but it cannot do that
    # here because nothing tells it what the closure's receiver is. Before
    # this consolidation, _ScanTab connected a bound method
    # (self._on_clean_done) and got that protection for free; guard it
    # explicitly here instead, or a delete finishing after `widget` is torn
    # down reaches into a deleted C++ object.
    def _deliver_done(result):
        if not widget_is_valid(widget):
            return
        on_done(*result)

    def _deliver_error(e):
        if not widget_is_valid(widget):
            return
        if on_error is not None:
            on_error(e)
        else:
            logger.error("run_clean_safe: background delete failed: %s", e)

    worker = Worker(_run)
    worker.signals.result.connect(_deliver_done)
    worker.signals.error.connect(_deliver_error)
    QThreadPool.globalInstance().start(worker)
    return worker
