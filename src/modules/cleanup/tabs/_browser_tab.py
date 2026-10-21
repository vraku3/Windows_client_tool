"""_BrowserCleanupTab -- Browser -> Profile -> Cache type, with research rows.

Every cache row is presented through the shared row facts (WP3): a safety
badge with text and shape, a verdict line and a tooltip from the research
knowledge base (``knowledge/browser_types.json``, keyed ``browser:<folder>``
for Chromium and ``browser:firefox:<folder>`` for Firefox). A cache type the
research does not cover says "Not yet researched" -- nothing is guessed from
the folder name.

Deletion is unchanged: the ticked, readable cache rows go to
``browser_scanner.delete_cache_entries``, which is ``delete_items_report``
(measured bytes, refusal guards, exclusions, deletion log). This tab only
decides what is TICKED; it never deletes anything itself.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QStackedWidget, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.table_ui import set_role
from core.widget_life import widget_is_valid as _alive
from core.worker import Worker
from modules.cleanup import browser_scanner as bs
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner._common import ScanItem
from modules.cleanup.components.detail_pane import DetailPane
from modules.cleanup.components.item_rows import (
    BADGES, FACTS_ROLE, SAFETY_ROLE, SHARE_ROLE, SIZE_ROLE, RowFacts,
)
from modules.cleanup.components.safety_delegate import HEADERS, SafetyDelegate
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

#: Owner decision (plan 2.3): a cache type research rates "Review" or "Risky"
#: starts UNTICKED. Flip to False to tick everything the scanner offers.
UNTICK_REVIEW_BY_DEFAULT = True

#: The browser scan measured ~2 s here; a lost completion must not leave the
#: tab stuck on "Scanning..." for the life of the process.
SCAN_WATCHDOG_MS = 120_000

#: Every browser cache the scanner offers is scoped to cache folders only
#: (CHROMIUM_CACHE_SUBDIRS / FIREFOX_CACHE_SUBDIRS), so its catalog safety is
#: "safe"; research may only raise it (knowledge.effective_safety).
CATALOG_SAFETY = "safe"

PAGE_CONTENT, PAGE_EMPTY, PAGE_LOADING = 0, 1, 2

_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 40     # "browser" | "profile" | "cache"
_ENTRY_ROLE = int(Qt.ItemDataRole.UserRole)          # CacheEntry on cache rows
_RANK = {"safe": 0, "caution": 1, "danger": 2}
_COL_NAME, _COL_SIZE, _COL_SHARE, _COL_SAFETY, _COL_VERDICT, _COL_NOTE = range(6)


def kb_id(engine: str, profile_path, cache_path) -> str:
    """The knowledge-base key of one cache folder, from where it sits.

    The relative folder under the profile is the identity
    (``Crashpad\\reports``), so a renamed display label never orphans the
    research. A cache outside its profile falls back to its folder name.
    """
    try:
        rel = os.path.relpath(str(cache_path), str(profile_path))
    except ValueError:                       # different drive
        rel = Path(cache_path).name
    if rel.startswith(".."):
        rel = Path(cache_path).name
    prefix = "browser:firefox:" if engine == "firefox" else "browser:"
    return prefix + rel


def build_cache_facts(result, profile, entry, total_bytes: int) -> RowFacts:
    """Row facts for one cache folder. Pure presentation, no I/O, no mutation."""
    catalog_id = kb_id(getattr(result, "engine", "chromium"), profile.path, entry.path)
    description = (f"The {entry.label} folder of {result.name}'s "
                   f"“{profile.name}” profile.")
    item = ScanItem(path=str(entry.path), size=int(entry.size_bytes or 0), is_dir=True,
                    selected=True, safety=CATALOG_SAFETY)
    facts = RowFacts.from_item(
        item, entry.label, catalog_id=catalog_id, category="browser",
        description=description, total_bytes=total_bytes,
        catalog_safety=CATALOG_SAFETY)
    chips = list(facts.chips)
    if result.is_running:
        # Not "in use by": which files it holds is only known after a clean.
        chips.insert(0, f"{result.name} running: close first")
    reason = ""
    if entry.locked:
        chips.insert(0, "Could not be read")
        reason = "This folder could not be read, so it is never offered for cleaning."
    elif UNTICK_REVIEW_BY_DEFAULT and facts.safety != "safe":
        reason = (f"Research rates this “{BADGES[facts.safety]}”, so it starts "
                  "unticked. Tick it yourself after reading what it holds.")
    return replace(facts, chips=tuple(chips), unselected_reason=reason,
                   selected=not reason)


def _confirm_clean(parent: QWidget, count: int, nbytes: int, running: list) -> bool:
    """Every browser clean confirms, defaulting to No."""
    text = (f"Delete the contents of <b>{count}</b> browser cache folder(s), "
            f"<b>{escape(cs.format_size(nbytes))}</b> as last measured?"
            "<br><br>The folders themselves stay; the browsers rebuild them.")
    if running:
        text += ("<br><br><b>Still running:</b> " + escape(", ".join(running))
                 + ". Files it holds open are left in place and listed afterwards; "
                 "close it first to clean everything.")
    box = QMessageBox(parent)
    box.setWindowTitle("Clean browser caches")
    box.setIcon(QMessageBox.Icon.Warning if running else QMessageBox.Icon.Question)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def _scan_job(_worker):
    """Worker body: the scan plus the exclusions, both read off the UI thread."""
    from modules.cleanup.cleanup_scanner.exclusions import list_exclusions
    return bs.scan_browsers_robust(), list_exclusions()


class _Item(QTreeWidgetItem):
    """Sorts Size and Share on the number, never the formatted text."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        if col in (_COL_SIZE, _COL_SHARE):
            role = SIZE_ROLE if col == _COL_SIZE else SHARE_ROLE
            return (self.data(col, role) or 0) < (other.data(col, role) or 0)
        if col == _COL_SAFETY:
            return (_RANK.get(self.data(col, SAFETY_ROLE), 2)
                    < _RANK.get(other.data(col, SAFETY_ROLE), 2))
        return self.text(col).casefold() < other.text(col).casefold()


class _BrowserCleanupTab(QWidget):
    """Browser -> Profile -> Cache type, research-labelled, measured cleaning."""
    freed_bytes = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanning = False
        self._cleaning = False
        self._scanned = False
        self._workers: list = []        # track ALL workers for cancellation
        self._scan_worker = None
        self._clean_worker = None
        self._running: list[str] = []
        self._total = 0
        self._last_clean = ""
        self._thread_pool = QThreadPool.globalInstance()
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(SCAN_WATCHDOG_MS)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._setup_ui()
        self._setup_shortcuts()

    # ── UI ──────────────────────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._build_toolbar(layout)

        self._build_feedback(layout)

        self._build_results(layout)

        legend = QLabel("✓ Safe: rebuilt by the browser.   ! Review: diagnostics or "
                        "data that does not come back; starts unticked.   Close a browser "
                        "before cleaning it, or the files it holds stay.")
        set_role(legend, "muted")
        legend.setWordWrap(True)
        legend.setToolTip("Safety is shown as text and shape, never colour alone.")
        layout.addWidget(legend)

        self._scan_btn.clicked.connect(self._do_scan)
        self._stop_btn.clicked.connect(self._stop_scan)
        self._clean_btn.clicked.connect(self._do_clean)

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        self._shortcuts = []
        for keys, slot in (("F5", self._do_scan),
                           ("Delete", self._do_clean),
                           ("Ctrl+A", self._select_all),
                           ("Ctrl+Shift+A", self._deselect_all),
                           ("F1", self._focus_detail),
                           ("Return", self._focus_detail)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(ctx)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

    # ── scan ────────────────────────────────────────────────────────────
    def auto_scan(self):
        if not self._scanned:
            self._do_scan()

    def _do_scan(self):
        if self._scanning or self._cleaning:
            return
        self._scanning = True
        self._scanned = True
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._clean_btn.setEnabled(False)
        self._error.clear()
        self._warn.hide()
        self._detail.clear()
        self._status.setText("Scanning browsers…")
        self._progress.setRange(0, 0)
        self._progress.show()
        self._stack.setCurrentIndex(PAGE_LOADING)

        worker = Worker(_scan_job)
        self._scan_worker = worker

        def _done(payload, w=worker):
            if _alive(self) and w is self._scan_worker:
                self._on_scan_result(payload)

        def _err(msg, w=worker):
            if _alive(self) and w is self._scan_worker:
                self._on_scan_error(msg)

        def _cancelled(w=worker):
            if _alive(self) and w is self._scan_worker:
                self._reset_scan_state("Scan stopped. Nothing was deleted.")

        worker.signals.result.connect(_done)
        worker.signals.error.connect(_err)
        worker.signals.cancelled.connect(_cancelled)
        self._workers.append(worker)
        self._watchdog.start()
        self._thread_pool.start(worker)

    def _reset_scan_state(self, status: str) -> None:
        self._watchdog.stop()
        self._scanning = False
        self._scan_worker = None
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.hide()
        self._status.setText(status)
        has_rows = self._tree.topLevelItemCount() > 0
        self._stack.setCurrentIndex(PAGE_CONTENT if has_rows else PAGE_EMPTY)
        self._clean_btn.setEnabled(bool(self._collect_checked()))
        if not has_rows:
            self._scanned = False          # the next visit scans again

    def _stop_scan(self):
        worker = self._scan_worker
        if worker is None:
            return
        worker.cancel()
        self._reset_scan_state("Scan stopped. Nothing was deleted.")

    def _on_watchdog(self):
        if not self._scanning:
            return
        logger.warning("Browser cache scan produced no result in %d s; resetting",
                       SCAN_WATCHDOG_MS // 1000)
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._reset_scan_state("The scan did not finish in time. Click Scan to try again.")

    def _on_scan_result(self, payload):
        results, excluded = payload if isinstance(payload, tuple) else (payload, [])
        self._watchdog.stop()
        self._scanning = False
        self._scan_worker = None
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.hide()
        self.populate(results, excluded)

    def populate(self, results, excluded=()) -> None:
        """Fill the tree from scan results. Presentation only."""
        from modules.cleanup.cleanup_scanner.exclusions import is_excluded

        excluded = list(excluded or [])
        hidden = 0
        shown = []       # (result, [(profile, [entries])])
        for result in results:
            profiles = []
            for profile in result.profiles:
                entries = []
                for entry in profile.caches:
                    if not entry.exists:
                        continue
                    if not entry.locked and int(entry.size_bytes or 0) == 0:
                        continue
                    if excluded and is_excluded(str(entry.path), excluded):
                        hidden += 1
                        continue
                    entries.append(entry)
                if entries:
                    profiles.append((profile, entries))
            if profiles:
                shown.append((result, profiles))

        total = sum(int(e.size_bytes or 0) for _r, ps in shown for _p, es in ps for e in es)
        self._total = total
        self._running = [r.name for r, _ps in shown if r.is_running]
        # "folder not found" is how the scanner says a browser is not installed;
        # only a real failure to read is worth a banner.
        errors = [f"{r.name}: {r.error}" for r in results
                  if getattr(r, "error", None) and not r.profiles
                  and "not found" not in str(r.error).lower()]

        self._tree.blockSignals(True)
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        caches = locked = review = 0
        for result, profiles in shown:
            b_size = sum(int(e.size_bytes or 0) for _p, es in profiles for e in es)
            b_item = self._parent_row(result.name, b_size, total, "browser")
            notes = ["Running — close first"] if result.is_running else []
            b_item.setText(_COL_NOTE, " · ".join(notes))
            b_item.setText(_COL_VERDICT, f"{len(profiles)} profile(s)")
            b_tip = (f"<b>{escape(result.name)}</b> ({escape(result.engine)})<br>"
                     f"{escape(cs.format_size(b_size))} in {len(profiles)} profile(s).")
            if result.is_running:
                b_tip += ("<br><b>Running.</b> Files it holds open are left in place; "
                          "close it before cleaning.")
            b_tip += "<br>Only cache folders are listed: never history, cookies or sign-ins."
            for col in range(len(HEADERS)):
                b_item.setToolTip(col, b_tip)
            self._tree.addTopLevelItem(b_item)
            worst = "safe"
            for profile, entries in profiles:
                p_size = sum(int(e.size_bytes or 0) for e in entries)
                p_item = self._parent_row(profile.name, p_size, total, "profile")
                p_item.setText(_COL_VERDICT, f"{len(entries)} cache type(s)")
                p_tip = (f"<b>{escape(result.name)} profile “{escape(profile.name)}”</b>"
                         f"<br>{escape(str(profile.path))}<br>"
                         f"{escape(cs.format_size(p_size))} in {len(entries)} cache folder(s).")
                for col in range(len(HEADERS)):
                    p_item.setToolTip(col, p_tip)
                b_item.addChild(p_item)
                p_worst = "safe"
                for entry in entries:
                    facts = build_cache_facts(result, profile, entry, total)
                    p_item.addChild(self._cache_row(facts, entry))
                    caches += 1
                    locked += bool(entry.locked)
                    review += facts.safety != "safe"
                    if _RANK[facts.safety] > _RANK[p_worst]:
                        p_worst = facts.safety
                self._set_safety(p_item, p_worst)
                if _RANK[p_worst] > _RANK[worst]:
                    worst = p_worst
                p_item.setExpanded(True)
            self._set_safety(b_item, worst)
            b_item.setExpanded(True)
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(_COL_SIZE, Qt.SortOrder.DescendingOrder)
        self._tree.blockSignals(False)

        if self._running:
            self._warn.setText(
                f"⚠  Running: {', '.join(self._running)} — close before cleaning, "
                "or the files it holds open stay.")
            self._warn.show()
        else:
            self._warn.hide()
        if errors:
            names = [e.split(":", 1)[0] for e in errors]
            shown_names = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
            self._error.set_error(f"Could not read {shown_names}. Hover for details.")
            self._error.setToolTip("\n".join(errors))

        if not shown:
            self._empty.setText("No browser caches found.\nNothing to clean here right now; "
                                "click Scan (F5) to look again.")
            self._stack.setCurrentIndex(PAGE_EMPTY)
            self._status.setText("No browser caches found."
                                 + (f" {hidden} excluded." if hidden else ""))
        else:
            self._stack.setCurrentIndex(PAGE_CONTENT)
            parts = [f"{len(shown)} browser(s)", f"{caches} cache folder(s)",
                     cs.format_size(total)]
            if review:
                parts.append(f"{review} to review (unticked)")
            if locked:
                parts.append(f"{locked} could not be read")
            if hidden:
                parts.append(f"{hidden} excluded")
            self._status.setText(" · ".join(parts))
        if self._last_clean:
            self._status.setText(f"{self._last_clean}. Now: {self._status.text()}")
            self._last_clean = ""
        self._refresh_clean_button()

    @staticmethod
    def _set_safety(item, safety: str) -> None:
        # The delegate colours BOTH the badge and the share bar from this role.
        for col in (_COL_SHARE, _COL_SAFETY):
            item.setData(col, SAFETY_ROLE, safety)

    def _parent_row(self, name: str, size: int, total: int, kind: str) -> _Item:
        item = _Item([name, cs.format_size(size), "", "", "", ""])
        item.setData(0, _KIND_ROLE, kind)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate
                      | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setData(_COL_SIZE, SIZE_ROLE, size)
        item.setData(_COL_SHARE, SHARE_ROLE, 100.0 * size / total if total else 0.0)
        item.setTextAlignment(_COL_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setData(0, Qt.ItemDataRole.AccessibleTextRole,
                     f"{name}, {cs.format_size(size)}")
        return item

    def _cache_row(self, facts: RowFacts, entry) -> _Item:
        size_text = "—" if entry.locked else facts.size_text
        item = _Item([facts.label, size_text, "", facts.badge_text, facts.verdict,
                      " · ".join(facts.chips)])
        item.setData(0, _KIND_ROLE, "cache")
        item.setData(0, _ENTRY_ROLE, entry)
        item.setData(0, FACTS_ROLE, facts)
        item.setData(_COL_SIZE, SIZE_ROLE, facts.size_bytes)
        item.setData(_COL_SHARE, SHARE_ROLE, facts.share_percent)
        self._set_safety(item, facts.safety)
        item.setTextAlignment(_COL_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        flags = item.flags()
        if entry.locked:
            flags &= ~Qt.ItemFlag.ItemIsUserCheckable
        else:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)
        item.setCheckState(0, Qt.CheckState.Checked if facts.selected
                           else Qt.CheckState.Unchecked)
        for col in range(len(HEADERS)):
            item.setToolTip(col, facts.tooltip)
        item.setData(0, Qt.ItemDataRole.AccessibleTextRole,
                     f"{facts.label}, {size_text}, {facts.badge_text}, {facts.verdict}")
        return item

    def _on_scan_error(self, err: str):
        self._reset_scan_state("The scan failed. Nothing was deleted.")
        self._error.set_error(f"Browser scan failed: {err}")

    # ── selection / detail ──────────────────────────────────────────────
    def _cache_items(self):
        for i in range(self._tree.topLevelItemCount()):
            b = self._tree.topLevelItem(i)
            for j in range(b.childCount()):
                p = b.child(j)
                for k in range(p.childCount()):
                    yield p.child(k)

    def _collect_checked(self) -> list:
        cats = []
        for c in self._cache_items():
            if c.checkState(0) != Qt.CheckState.Checked:
                continue
            entry = c.data(0, _ENTRY_ROLE)
            if entry is None or getattr(entry, "locked", False):
                continue      # an unreadable folder is never offered
            cats.append(entry)
        return cats

    def _refresh_clean_button(self):
        checked = self._collect_checked()
        self._clean_btn.setEnabled(bool(checked) and not self._scanning and not self._cleaning)
        if checked:
            size = sum(int(e.size_bytes or 0) for e in checked)
            self._clean_btn.setToolTip(
                f"Delete the contents of {len(checked)} ticked cache folder(s), "
                f"{cs.format_size(size)} (Delete key). Always asks first.")

    def _on_item_changed(self, item, column):
        if column == 0 and not self._tree.signalsBlocked():
            self._refresh_clean_button()

    def _on_current_changed(self, current, _previous):
        if not _alive(self) or current is None:
            self._detail.clear()
            return
        facts = current.data(0, FACTS_ROLE)
        if isinstance(facts, RowFacts):
            ticked = current.checkState(0) == Qt.CheckState.Checked
            self._detail.set_row(replace(facts, selected=ticked))
            return
        self._detail.clear()
        kind = current.data(0, _KIND_ROLE)
        children = current.childCount()
        self._detail.badge.setText(current.text(_COL_SIZE))
        self._detail.browser.setHtml(
            f"<h2>{escape(current.text(0))}</h2>"
            f"<p>{escape(current.text(_COL_SIZE))} · "
            f"{'profile' if kind == 'profile' else 'browser'} with {children} "
            f"{'cache type(s)' if kind == 'profile' else 'profile(s)'}.</p>"
            f"<p>{escape(current.text(_COL_NOTE))}</p>"
            "<p>Select a cache type below it to see what it is, what cleaning it "
            "costs and where the research comes from.</p>")

    def _focus_detail(self):
        if self._tree.currentItem() is not None:
            self._detail.browser.setFocus()

    def _select_all(self):
        self._tree.blockSignals(True)
        for c in self._cache_items():
            facts = c.data(0, FACTS_ROLE)
            entry = c.data(0, _ENTRY_ROLE)
            if entry is not None and not entry.locked and getattr(facts, "safety", "") == "safe":
                c.setCheckState(0, Qt.CheckState.Checked)
        self._tree.blockSignals(False)
        self._tree.viewport().update()
        self._refresh_clean_button()

    def _deselect_all(self):
        self._tree.blockSignals(True)
        for c in self._cache_items():
            c.setCheckState(0, Qt.CheckState.Unchecked)
        self._tree.blockSignals(False)
        self._tree.viewport().update()
        self._refresh_clean_button()

    # ── path actions requested by the detail pane ───────────────────────
    def _open_folder(self, path: str):
        target = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            os.startfile(target)
        except OSError as exc:
            logger.warning("Could not open %s: %s", target, exc)
            self._error.set_error(f"Could not open {target}: {exc}")

    def _copy_path(self, path: str):
        QApplication.clipboard().setText(path)
        self._status.setText(f"Copied: {path}")

    def _exclude_path(self, path: str):
        from modules.cleanup.cleanup_scanner import exclusions
        if not exclusions.add_exclusion(path):
            QMessageBox.information(self, "Already Excluded",
                                    f"“{path}” is already on the exclusion list.")
            return
        logger.info("Cleanup: excluded %s from all future scans", path)
        QMessageBox.information(
            self, "Path Excluded",
            f"“{path}” will no longer be offered or cleaned by any Cleanup tab.\n\n"
            "Manage exclusions from the module's \"Exclusions…\" button.")
        if self._scanned:
            self._do_scan()

    # ── clean ───────────────────────────────────────────────────────────
    def _do_clean(self):
        if self._cleaning or self._scanning:
            return
        cats = self._collect_checked()
        if not cats:
            return
        total = sum(int(c.size_bytes or 0) for c in cats)
        if not _confirm_clean(self, len(cats), total, list(self._running)):
            return
        self._cleaning = True
        self._clean_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._status.setText("Cleaning…")
        self._progress.setRange(0, 0)
        self._progress.show()

        worker = Worker(lambda _w: bs.delete_cache_entries(cats))
        self._clean_worker = worker

        def _done(result):
            if _alive(self):
                self._on_clean_done(result)

        def _err(msg):
            if _alive(self):
                self._on_clean_error(msg)

        def _cancelled():
            if _alive(self):
                self._on_clean_error("the clean was interrupted before it reported")

        worker.signals.result.connect(_done)
        worker.signals.error.connect(_err)
        worker.signals.cancelled.connect(_cancelled)
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _on_clean_done(self, result):
        freed, errors = result
        self._cleaning = False
        self._clean_worker = None
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        msg = f"Freed {cs.format_size(freed)} (measured)"
        if errors:
            msg += (f" · {errors} folder(s) not fully cleaned (in use or refused; "
                    "see the History tab)")
        self._status.setText(msg)
        self._clean_btn.setEnabled(False)
        self.freed_bytes.emit(int(freed))
        self._last_clean = msg
        self._do_scan()

    def _on_clean_error(self, err: str):
        self._cleaning = False
        self._clean_worker = None
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText("The clean did not finish.")
        self._error.set_error(f"Browser clean failed: {err}")
        self._refresh_clean_button()

    def _cancel_all(self) -> None:
        """Cancel the scan; a running clean is left to finish and report.

        `delete_cache_entries` never reads the cancel flag, so cancelling a
        clean would stop nothing except its MEASURED result reaching the
        session counter. The scan's state is reset here because a cancelled
        Worker emits `cancelled`, never `result`.
        """
        for w in self._workers:
            if w is not self._clean_worker:
                w.cancel()
        self._workers = [w for w in self._workers if w is self._clean_worker]
        if self._scanning:
            self._reset_scan_state("Scan stopped. Nothing was deleted.")

    def _build_toolbar(self, layout):
        tb = QHBoxLayout()
        tb.setSpacing(6)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setToolTip("Measure every browser's cache folders again (F5). "
                                  "Read-only: nothing is deleted.")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop the running scan. Nothing has been deleted.")
        self._stop_btn.setEnabled(False)
        self._clean_btn = QPushButton("Clean selected…")
        self._clean_btn.setObjectName("accentButton")
        self._clean_btn.setToolTip(
            "Delete the contents of the ticked cache folders (Delete key). "
            "Always asks first. The folders stay; files a running browser "
            "holds open are left in place and listed.")
        self._clean_btn.setEnabled(False)
        self._more_btn = QToolButton()
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("More: select all safe, deselect all, expand or collapse")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._more_btn)
        self._act_sel = menu.addAction("Select all safe", self._select_all)
        self._act_sel.setShortcut(QKeySequence("Ctrl+A"))
        self._act_sel.setToolTip("Tick every readable cache rated Safe (Ctrl+A).")
        self._act_desel = menu.addAction("Deselect all", self._deselect_all)
        self._act_desel.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self._act_desel.setToolTip("Untick every cache (Ctrl+Shift+A).")
        menu.addSeparator()
        menu.addAction("Expand all", lambda: self._tree.expandAll())
        menu.addAction("Collapse to browsers", lambda: self._tree.collapseAll())
        menu.setToolTipsVisible(True)
        self._more_btn.setMenu(menu)
        for w, name in ((self._scan_btn, "Scan browser caches"),
                        (self._stop_btn, "Stop scan"),
                        (self._clean_btn, "Clean selected browser caches"),
                        (self._more_btn, "More selection actions")):
            w.setAccessibleName(name)
            tb.addWidget(w)
        self._status = QLabel("Not scanned yet")
        self._status.setAccessibleName("Browser cache status")
        set_role(self._status, "muted")
        self._status.setMinimumWidth(120)
        tb.addSpacing(8)
        tb.addWidget(self._status, 1)
        layout.addLayout(tb)

    def _build_feedback(self, layout):
        self._error = ErrorBanner()
        layout.addWidget(self._error)

        self._warn = QLabel()
        self._warn.setObjectName("statusWarning")
        self._warn.setWordWrap(True)
        self._warn.setToolTip("A running browser holds some of its cache files open. "
                              "Those files are left in place; close the browser first "
                              "to clean everything.")
        self._warn.hide()
        layout.addWidget(self._warn)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

    def _build_results(self, layout):
        self._stack = QStackedWidget()
        # Page 0: tree + detail pane
        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(HEADERS))
        self._tree.setHeaderLabels(["Browser / Profile / Cache type", *HEADERS[1:]])
        self._tree.setAccessibleName("Browser caches")
        self._tree.setUniformRowHeights(False)
        self._tree.setAlternatingRowColors(False)
        self._tree.setItemDelegate(SafetyDelegate(self._tree, safety_column=_COL_SAFETY,
                                                  share_column=_COL_SHARE))
        hdr = self._tree.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Interactive)
        self._tree.setColumnWidth(_COL_NAME, 360)
        self._tree.setColumnWidth(_COL_SIZE, 90)
        self._tree.setColumnWidth(_COL_SHARE, 130)
        self._tree.setColumnWidth(_COL_SAFETY, 100)
        self._tree.setColumnWidth(_COL_VERDICT, 190)
        for col, tip in ((_COL_NAME, "Tick a cache type to clean it. Hover a row for what it is."),
                         (_COL_SIZE, "Size as last measured"),
                         (_COL_SHARE, "Share of all browser caches found"),
                         (_COL_SAFETY, "Safe, Review or Risky, from research (never less "
                                       "cautious than the catalog)"),
                         (_COL_VERDICT, "Whether the data comes back after cleaning"),
                         (_COL_NOTE, "Running browser, unreadable folder, kept files")):
            self._tree.headerItem().setToolTip(col, tip)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.itemChanged.connect(self._on_item_changed)

        self._detail = DetailPane()
        # A long path label without spaces would otherwise set the pane's
        # minimum, and with it the whole tab's (measured: 954 px).
        self._detail.setMinimumWidth(260)
        self._detail.setToolTip("Explanation, paths and research sources for the selected row")
        self._detail.open_folder.connect(self._open_folder)
        self._detail.copy_path.connect(self._copy_path)
        self._detail.exclude_path.connect(self._exclude_path)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._tree)
        self._splitter.addWidget(self._detail)
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, True)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([900, 340])
        self._stack.addWidget(self._splitter)

        # Page 1: empty state
        self._empty = QLabel("Browser caches have not been measured yet.\n"
                             "Click Scan (F5) to look at every installed browser.")
        self._empty.setObjectName("emptyState")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack.addWidget(self._empty)

        # Page 2: loading state
        self._loading = QLabel("Measuring browser caches…\n"
                               "Read-only; this usually takes a few seconds.")
        self._loading.setObjectName("emptyState")
        self._loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading.setWordWrap(True)
        self._stack.addWidget(self._loading)
        self._stack.setCurrentIndex(PAGE_EMPTY)
        layout.addWidget(self._stack, 1)
