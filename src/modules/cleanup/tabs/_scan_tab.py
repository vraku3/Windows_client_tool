"""_ScanTab -- the area tabs (System Junk, Apps & Games, Windows Update, Logs,
Developer Tools, Large Items): scan many scanners, review, clean.

Every row is presented through the shared row facts (WP3,
``components/item_rows.py``): a safety badge with text and shape, a share
bar, a verdict line and a tooltip from the research knowledge base
(``cleanup_scanner/knowledge.py``), keyed by the scanner's catalog id. A
location the research does not cover says "Not yet researched" and shows the
catalog's own description -- nothing is guessed from a folder name.

Deletion is unchanged: the ticked rows go to ``clean_safe_runner.run_clean_safe``
-> ``delete_items_report`` (measured bytes, refusal guards, exclusions,
deletion log). This tab only decides what is TICKED. Review and Risky rows
start unticked (``UNTICK_REVIEW_BY_DEFAULT``), and a row hidden by the filter
is never cleaned: "Clean selected" acts on what can be seen.
"""
from __future__ import annotations

import logging
import ntpath
import os
from dataclasses import replace
from html import escape
from typing import Callable, Iterable, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QSizePolicy, QWidget, QWidgetAction,
)

from core.semantic_colors import chrome, semantic
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup import clean_safe_runner as csr
from modules.cleanup.cleanup_scanner import breakdown, exclusions, scan_cache
from modules.cleanup.components.detail_pane import DetailPane
from modules.cleanup.components.item_rows import (
    BADGES, COLLAPSE_THRESHOLD, FACTS_ROLE, SAFETY_ROLE, SHARE_ROLE, SIZE_ROLE,
    RowFacts, collapse_rows, knowledge_api,
)
from modules.cleanup.components.safety_delegate import HEADERS, SafetyDelegate
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)


# ── Safety colour helpers (shared with other tabs) ────────────────────────────

_SAFETY_LABELS = {"safe": "Safe", "caution": "Review", "danger": "Risky"}
_SAFETY_MEANINGS = {"safe": "success", "caution": "warning", "danger": "error"}


class _SafetyStyles(dict):
    """``{level: (colour, label)}``, the colour resolved for the CURRENT theme.

    It used to be a dict of frozen hex colours (D10): the old orange on the
    light pane was about 2.2:1. Reading through ``semantic()`` at access time keeps
    the old name and shape working for any caller, in every theme.
    """

    def __init__(self):
        super().__init__({level: None for level in _SAFETY_LABELS})

    def __getitem__(self, level):
        if level not in _SAFETY_LABELS:
            raise KeyError(level)
        return safety_style(level)

    def get(self, level, default=None):
        return safety_style(level) if level in _SAFETY_LABELS else default

    def items(self):
        return [(level, safety_style(level)) for level in _SAFETY_LABELS]

    def values(self):
        return [safety_style(level) for level in _SAFETY_LABELS]


def safety_style(level: str) -> tuple[str, str]:
    """(colour for the current theme, word) for a safety level."""
    meaning = _SAFETY_MEANINGS.get(level)
    if meaning is None:
        return chrome("text_muted"), ""
    return semantic(meaning), _SAFETY_LABELS[level]


SAFETY_STYLES = _SafetyStyles()


def __getattr__(name: str):
    # MUTED used to be a frozen grey; it is now the theme's muted text,
    # read when asked for (PEP 562), so the old import keeps working.
    if name == "MUTED":
        return chrome("text_muted")
    raise AttributeError(name)


CONFIRM_BYTES = 500 * 1024 * 1024   # 500 MB

#: Owner decision (plan 2.3 / 3.4): a location rated "Review" or "Risky" --
#: by the catalog or, more cautiously, by research -- starts UNTICKED on the
#: area tabs. A scanner's own ``selected=False`` is always honoured. Flip to
#: False to tick everything the scanners select.
UNTICK_REVIEW_BY_DEFAULT = True

PAGE_CONTENT, PAGE_EMPTY, PAGE_LOADING = 0, 1, 2

# Column-1 UserRole markers for the lazy breakdown of an oversized item.
_NEEDS_BREAKDOWN = "needs-breakdown"
_BREAKDOWN_RUNNING = "breakdown-running"
_BREAKDOWN_DONE = "breakdown-done"

_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 40   # "group" | "summary" | "item" | "inner"
_RANK = {"safe": 0, "caution": 1, "danger": 2}
_COL_NAME, _COL_SIZE, _COL_SHARE, _COL_SAFETY, _COL_VERDICT, _COL_NOTE = range(6)
#: Paths a group (or folded "N files") row lists in the detail pane, each
#: with Open/Copy/Exclude; the rest are one click away in the tree.
_DETAIL_PATHS = 3
_CHIPS = (("all", "All"), ("safe", BADGES["safe"]), ("caution", BADGES["caution"]),
          ("danger", BADGES["danger"]), ("in_use", "In use last time"))

# A breakdown worker can finish after this tab is gone. See the same guard,
# and the reason for `from PyQt6 import sip` rather than `import sip`, in
# core/widget_life.py.
_alive = widget_is_valid


def _sc(level: str) -> str:
    return safety_style(level)[0]


def _confirm_large(parent: QWidget, nbytes: int) -> bool:
    if nbytes < CONFIRM_BYTES:
        return True
    mb = QMessageBox(parent)
    mb.setWindowTitle("Confirm Delete")
    mb.setIcon(QMessageBox.Icon.Warning)
    mb.setText(
        f"You are about to permanently delete <b>{cs.format_size(nbytes)}</b>."
        "<br>This cannot be undone."
    )
    mb.setStandardButtons(
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
    )
    mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return mb.exec() == QMessageBox.StandardButton.Ok


def _catalog_meta(fn) -> tuple[str, str, str]:
    """(catalog id, category, description) of a scanner, without I/O."""
    api = knowledge_api()
    if api is not None:
        cid = api.id_for_scanner(fn)
    else:
        spec = getattr(fn, "spec", None)
        cid = spec.id if spec is not None else getattr(fn, "__name__", "").removeprefix("scan_")
    spec = getattr(fn, "spec", None)
    return (cid, getattr(spec, "category", "") or "",
            getattr(spec, "description", "") or "")


def default_ticked(item: cs.ScanItem, facts: RowFacts) -> tuple[bool, str]:
    """Whether a row starts ticked, and if not, the sentence that says why.

    Pure: the scan result is never mutated. Unticking only ever removes a
    row from what would be deleted; it never adds one.
    """
    if not item.selected:
        return False, ("The scanner itself leaves this unticked: it is offered for "
                       "review, not for a routine clean.")
    if UNTICK_REVIEW_BY_DEFAULT and facts.safety != "safe":
        return False, (f"Rated “{BADGES[facts.safety]}”, so it starts unticked. "
                       "Tick it yourself after reading what it holds.")
    return True, ""


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


# ── _ScanTab ──────────────────────────────────────────────────────────────────

class _ScanTab(QWidget):
    """
    Generic scan/clean tab supporting multiple scanners.

    scanners: dict  { scanner_fn: (display_label, safety_level) }
    wu_cache: True if wuauserv must be stopped during clean.
    """
    freed_bytes = pyqtSignal(int)

    #: Give up on a scan that has not finished in this long, hand the tab
    #: back, and say what never reported. The worst measured sweep on a real
    #: machine is 35.6s (System Junk, elevated), so this cannot fire on a
    #: healthy scan — if it fires, something is wrong and a dead tab is the
    #: worst possible way to find out.
    SCAN_WATCHDOG_MS = 300_000

    def __init__(self, scanners: dict, wu_cache: bool = False, parent=None):
        super().__init__(parent)
        self._scanners = scanners
        self._wu_cache = wu_cache
        self._result: Optional[cs.ScanResult] = None
        self._scanning = False
        self._cleaning = False
        self._scanned  = False
        self._workers: list = []   # track ALL workers (scan + clean) for cancellation
        self._scan_fns: list = []  # the scanners of the run in flight, in order
        self._scan_worker = None
        self._clean_worker = None
        self._breakdown_workers = {}
        self._locked_paths = ()
        self._total = 0
        self._counts: dict = {}
        self._chip = "all"
        self._last_clean = ""
        #: Optional ``path -> [other tab names]`` supplied by the module
        #: (cross-tab snapshot), so a location listed on several tabs says so.
        self.also_on_provider: Optional[Callable[[str], Iterable[str]]] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_scan_watchdog)
        self._setup_ui()
        self._setup_shortcuts()

    # ── Setup ──

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._build_toolbar(layout)

        self._build_filters(layout)

        self._build_feedback(layout)

        self._build_results(layout)

        legend = QLabel("✓ Safe: rebuilt or not needed.   ! Review: read first; starts "
                        "unticked.   ✕ Risky: may not come back; starts unticked.")
        set_role(legend, "muted")
        legend.setWordWrap(True)
        legend.setToolTip("Safety is shown as text and shape, never colour alone.")
        layout.addWidget(legend)

        self._scan_btn.clicked.connect(self._do_scan)
        self._stop_btn.clicked.connect(self._stop_scan)
        self._clean_btn.clicked.connect(self._do_clean)

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        self._shortcuts = {}
        for keys, slot in (("F5", self._do_scan),
                           ("Delete", self._shortcut_delete),
                           ("Ctrl+A", self._shortcut_select_all),
                           ("Ctrl+Shift+A", self._deselect_all),
                           ("Ctrl+F", self._focus_filter),
                           ("F1", self._focus_detail),
                           ("Return", self._focus_detail)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(ctx)
            sc.activated.connect(slot)
            self._shortcuts[keys] = sc
        # Space toggles the current row's tick natively (the delegate's
        # editorEvent); a Space shortcut would also steal it from the filter.

    # ── Public ──

    def auto_scan(self):
        """Scan automatically on first activation."""
        if not self._scanned:
            self._do_scan()

    # ── Scan ──

    def _do_scan(self):
        if self._scanning or self._cleaning:
            return
        self._scanning = True
        self._scanned  = True
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)
        self._error.clear()
        self._detail.clear()
        self._cancel_breakdowns()
        self._tree.clear()

        min_age = self._age_spin.value()
        scanner_fns = list(self._scanners.keys())
        self._scan_fns = scanner_fns

        # A determinate bar over the scanner count, not a barber's pole.
        # System Junk is 75 scanners and App & Game Caches is 301; the tab
        # used to say "Scanning…" for all of them and nothing else.
        self._progress.setRange(0, len(scanner_fns))
        self._progress.setValue(0)
        self._progress.show()
        self._status.setText(f"Scanning 0/{len(scanner_fns)}…")
        self._loading.setText(f"Measuring {len(scanner_fns)} location group(s)…\n"
                              "Read-only; nothing is deleted.")
        self._stack.setCurrentIndex(PAGE_LOADING)

        def _run(worker):
            per: dict = {}
            failed: list = []
            for index, fn in enumerate(scanner_fns, start=1):
                # Worker.cancel() only sets a flag — this is what makes Stop
                # (and switching modules) actually stop the sweep instead of
                # letting it run to completion on the shared pool.
                if worker.is_cancelled:
                    logger.info("Scan stopped after %d/%d scanners",
                                index - 1, len(scanner_fns))
                    break
                worker.signals.progress.emit(index)
                try:
                    r = scan_cache.cached_scan(fn, min_age)
                    if r:
                        per[fn] = r
                except Exception as e:
                    logger.warning(f"Scan function {fn.__name__} failed: {e}")
                    failed.append((fn, str(e)))
            merged = cs.ScanResult()
            for r in per.values():
                merged.items.extend(r.items)
            # Two scanners can legitimately find the same directory —
            # %TEMP% and %LOCALAPPDATA%\Temp are the same path, so
            # scan_temp_files and scan_user_crash_dumps both measured
            # 39.4 GB here and summing them offered 78.8 GB of "junk" for
            # 39.4 GB of files. It also had Clean deleting the same tree
            # twice.
            merged.items = cs.dedupe_items(merged.items)
            merged.total_size = sum(i.size for i in merged.items)
            return merged, per, failed

        worker = Worker(_run)
        self._scan_worker = worker

        # Closures, guarded: a scan can land after this tab is gone.
        def _progress(index, w=worker):
            if _alive(self) and w is self._scan_worker:
                self._on_scan_progress(index)

        def _done(payload, w=worker):
            if _alive(self) and w is self._scan_worker:
                self._on_scan_result(payload)

        def _err(msg, w=worker):
            if _alive(self) and w is self._scan_worker:
                self._on_scan_error(msg)

        def _cancelled(w=worker):
            # A cancelled Worker emits only `cancelled`; without this the tab
            # would sit on "Scanning..." for the life of the process.
            if _alive(self) and w is self._scan_worker and self._scanning:
                self._reset_after_cancel()

        worker.signals.progress.connect(_progress)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_err)
        worker.signals.cancelled.connect(_cancelled)
        self._watchdog.start(self.SCAN_WATCHDOG_MS)
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _label_of(self, fn) -> str:
        meta = self._scanners.get(fn)
        return meta[0] if meta else getattr(fn, "__name__", "?")

    def _on_scan_progress(self, index: int) -> None:
        """Name the scanner now running, and how far through the list it is."""
        if not self._scanning:
            return
        total = len(self._scan_fns)
        self._progress.setValue(index)
        label = self._label_of(self._scan_fns[index - 1]) if 0 < index <= total else ""
        self._status.setText(f"Scanning {index}/{total} — {label}")
        self._loading.setText(f"Measuring {index} of {total}: {label}\n"
                              "Read-only; nothing is deleted.")

    def _stop_scan(self) -> None:
        """Stop after the scanner now running, keeping whatever was found."""
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._cancel_all()

    # ── Breakdown of an oversized item ──

    def _on_item_expanded(self, node: QTreeWidgetItem) -> None:
        if node.data(1, Qt.ItemDataRole.UserRole) == _NEEDS_BREAKDOWN:
            self._fill_breakdown(node)

    @staticmethod
    def _placeholder(text: str) -> QTreeWidgetItem:
        row = QTreeWidgetItem([text, ""])
        row.setFlags(Qt.ItemFlag.NoItemFlags)
        row.setToolTip(0, text)
        return row

    def _fill_breakdown(self, node: QTreeWidgetItem) -> None:
        """Replace the placeholder with what is actually inside.

        Measured on a worker: a breakdown of %TEMP% walks 46,825
        directories, and doing that on the UI thread freezes the app for
        the three seconds it takes.
        """
        scan_item = node.data(0, Qt.ItemDataRole.UserRole)
        if scan_item is None:
            return
        parent_facts = node.data(0, FACTS_ROLE)
        node.setData(1, Qt.ItemDataRole.UserRole, _BREAKDOWN_RUNNING)
        node.takeChildren()
        node.addChild(self._placeholder("Measuring…"))

        path = scan_item.path
        safety = scan_item.safety
        # A child of %TEMP% is still %TEMP%: it keeps the parent's age floor,
        # or ticking one row of the breakdown deletes an installer's files
        # out from under it (final review, 2026-10-11).
        floor_hours = scan_item.keep_newer_than_hours

        def _run(_worker):
            return breakdown.children_by_size(path)

        def _done(rows):
            if not self._breakdown_is_current(node, worker):
                return
            self._tree.blockSignals(True)
            node.takeChildren()
            node.setData(1, Qt.ItemDataRole.UserRole, _BREAKDOWN_DONE)
            if not rows:
                node.addChild(self._placeholder("(nothing readable inside)"))
                self._tree.blockSignals(False)
                return
            for child_path, size in rows:
                inner = cs.ScanItem(path=child_path, size=size,
                                    is_dir=os.path.isdir(child_path),
                                    selected=False, safety=safety, keep_root=False,
                                    keep_newer_than_hours=floor_hours)
                facts = RowFacts.from_item(
                    inner, os.path.basename(child_path) or child_path,
                    catalog_id=getattr(parent_facts, "catalog_id", ""),
                    category=getattr(parent_facts, "category", ""),
                    description=getattr(parent_facts, "description", ""),
                    total_bytes=self._total,
                    catalog_safety=getattr(parent_facts, "catalog_safety", safety))
                facts = replace(facts, subtitle="", selected=False,
                                unselected_reason="Rows inside a folder start unticked: "
                                                  "tick only what you have checked.")
                row = self._row_item(facts, inner, "inner")
                row.setCheckState(0, Qt.CheckState.Unchecked)
                node.addChild(row)
            self._tree.blockSignals(False)
            self._apply_filter()

        def _err(message: str):
            if not self._breakdown_is_current(node, worker):
                return
            node.takeChildren()
            node.setData(1, Qt.ItemDataRole.UserRole, _BREAKDOWN_DONE)
            node.addChild(self._placeholder(f"(could not read: {message})"))

        def _cancelled():
            if self._breakdown_is_current(node, worker):
                node.takeChildren()
                node.setData(1, Qt.ItemDataRole.UserRole, _NEEDS_BREAKDOWN)
                node.addChild(self._placeholder("Expand to see what is inside…"))

        worker = Worker(_run)
        self._breakdown_workers[id(node)] = worker
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_err)
        worker.signals.cancelled.connect(_cancelled)
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _breakdown_is_current(self, node, worker) -> bool:
        return (_alive(self) and not sip.isdeleted(node)
                and self._breakdown_workers.get(id(node)) is worker)

    def _cancel_breakdowns(self):
        retire(list(self._breakdown_workers.values()))
        self._breakdown_workers.clear()

    def _read_last_locks(self):
        from modules.cleanup import cleanup_history
        entries = cleanup_history.recent_deleted(limit=1)
        self._locked_paths = tuple(ntpath.normcase(ntpath.normpath(path))
                                   for path in (entries[0].get("locked", ()) if entries else ())
                                   if isinstance(path, str))

    def _was_in_use(self, path):
        key = ntpath.normcase(ntpath.normpath(path))
        return any(p == key or p.startswith(key.rstrip("\\") + "\\")
                   for p in self._locked_paths)

    # ── Rows ──

    @staticmethod
    def _set_safety(item: QTreeWidgetItem, safety: str) -> None:
        # The delegate colours BOTH the badge and the share bar from this role.
        for col in (_COL_SHARE, _COL_SAFETY):
            item.setData(col, SAFETY_ROLE, safety)

    def _row_item(self, facts: RowFacts, scan_item, kind: str) -> _Item:
        item = _Item([facts.label, facts.size_text, "", facts.badge_text, facts.verdict,
                      " · ".join(facts.chips)])
        item.setData(0, _KIND_ROLE, kind)
        item.setData(0, FACTS_ROLE, facts)
        if scan_item is not None:
            item.setData(0, Qt.ItemDataRole.UserRole, scan_item)
        item.setData(_COL_SIZE, SIZE_ROLE, facts.size_bytes)
        item.setData(_COL_SHARE, SHARE_ROLE, facts.share_percent)
        self._set_safety(item, facts.safety)
        item.setTextAlignment(_COL_SIZE, Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
        if kind in ("group", "summary"):
            flags |= Qt.ItemFlag.ItemIsAutoTristate
        item.setFlags(flags)
        tip = facts.tooltip
        if facts.path and facts.path != facts.label:
            tip += f"<p>{escape(facts.path)}</p>"
        for col in range(len(HEADERS)):
            item.setToolTip(col, tip)
        item.setData(0, Qt.ItemDataRole.AccessibleTextRole,
                     f"{facts.label}, {facts.size_text}, {facts.badge_text}, {facts.verdict}")
        return item

    def _also_on(self, path: str) -> tuple:
        provider = self.also_on_provider
        if provider is None:
            return ()
        try:
            return tuple(provider(path) or ())
        except Exception as exc:          # a provider bug must not lose the scan
            logger.warning("Cleanup: also-on lookup failed for %s: %s", path, exc)
            return ()

    def _on_scan_result(self, data):
        merged, per_scanner = data[0], data[1]
        failed = data[2] if len(data) > 2 else []
        self._watchdog.stop()
        self._result  = merged
        self._scanning = False
        self._scan_worker = None
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.hide()
        self.populate(merged, per_scanner)
        if failed:
            names = [self._label_of(fn) for fn, _msg in failed]
            shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more"
                                            if len(names) > 3 else "")
            self._error.set_error(f"{len(names)} location group(s) could not be read: "
                                  f"{shown}. What they hold is not listed; hover for details.")
            self._error.setToolTip("\n".join(f"{self._label_of(fn)}: {msg}"
                                             for fn, msg in failed))

    def populate(self, merged: cs.ScanResult, per_scanner: dict) -> None:
        """Fill the tree from a scan. Presentation only: nothing is deleted
        and no ScanItem's ``selected`` is changed here."""
        self._cancel_breakdowns()
        self._read_last_locks()
        # path -> the scanner that found it (first wins, as dedupe keeps it)
        path_fn: dict = {}
        for fn in self._scanners:
            r = per_scanner.get(fn)
            if r:
                for item in r.items:
                    path_fn.setdefault(item.path, fn)

        grouped: dict = {}
        for item in merged.items:
            fn = path_fn.get(item.path)
            grouped.setdefault(fn, []).append(item)

        total = sum(i.size for i in merged.items)
        self._total = total
        counts = {"safe": 0, "caution": 0, "danger": 0}
        ticked_bytes = 0

        self._tree.blockSignals(True)
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        for fn, items in sorted(grouped.items(),
                                key=lambda kv: sum(i.size for i in kv[1]), reverse=True):
            label, safety = (self._scanners.get(fn) or ("Other", items[0].safety))
            cid, category, description = _catalog_meta(fn) if fn is not None else ("", "", "")
            items = sorted(items, key=lambda x: x.size, reverse=True)
            g_total = sum(i.size for i in items)

            child_facts = []
            for item in items:
                facts = RowFacts.from_item(
                    item, item.path, catalog_id=cid, category=category,
                    description=description, total_bytes=total, catalog_safety=safety,
                    also_on=self._also_on(item.path))
                ticked, reason = default_ticked(item, facts)
                if self._was_in_use(item.path):
                    facts = replace(facts, chips=(*facts.chips, "In use last time"))
                child_facts.append(replace(facts, subtitle="", selected=ticked,
                                           unselected_reason=reason))
            worst = max((f.safety for f in child_facts), key=_RANK.get)
            g_item = cs.ScanItem(path="", size=g_total, is_dir=True,
                                 selected=all(f.selected for f in child_facts),
                                 safety=worst)
            g_facts = RowFacts.from_item(
                g_item, label, catalog_id=cid, category=category, description=description,
                total_bytes=total, catalog_safety=safety)
            g_facts = replace(g_facts, children=tuple(child_facts[:_DETAIL_PATHS]),
                              verdict=g_facts.verdict if g_facts.entry is not None
                              else f"{len(items)} location(s)")
            parent = self._row_item(g_facts, None, "group")
            parent.setText(_COL_NOTE, f"{len(items)} location(s)")
            self._tree.addTopLevelItem(parent)

            # A long file-only group folds into one "N files" row; opening it
            # is "show all" (plan 2.3: 30 thumbcache files of 24 B each).
            folded = collapse_rows(child_facts, threshold=COLLAPSE_THRESHOLD)
            holder = parent
            if folded and folded[0].kind == "summary":
                summary = replace(folded[0], label=f"{folded[0].label} — open to show all",
                                  children=folded[0].children[:_DETAIL_PATHS],
                                  tooltip=(f"<b>{escape(folded[0].label)}</b><p>Open this "
                                           "row to see and tick each file.</p>"))
                holder = self._row_item(summary, None, "summary")
                parent.addChild(holder)

            for item, facts in zip(items, child_facts):
                child = self._row_item(facts, item, "item")
                child.setCheckState(0, Qt.CheckState.Checked if facts.selected
                                    else Qt.CheckState.Unchecked)
                holder.addChild(child)
                counts[facts.safety] = counts.get(facts.safety, 0) + 1
                if facts.selected:
                    ticked_bytes += facts.size_bytes

                # A directory too big to be a single checkbox gets a
                # placeholder, filled in when someone opens it. %TEMP% here
                # is 47.36 GB in one row; measuring every such item up front
                # would repeat the most expensive part of the sweep for rows
                # nobody expands.
                if breakdown.is_worth_expanding(item):
                    child.setData(1, Qt.ItemDataRole.UserRole, _NEEDS_BREAKDOWN)
                    child.addChild(self._placeholder("Expand to see what is inside…"))

            # A group's initial check state must reflect its children's
            # ScanItem.selected values (and the review policy), never force
            # everything to Checked -- a group can hold a mix.
            if all(f.selected for f in child_facts):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif not any(f.selected for f in child_facts):
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent.setExpanded(True)
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(_COL_SIZE, Qt.SortOrder.DescendingOrder)
        self._tree.blockSignals(False)

        self._counts = counts
        self._update_chip_texts()
        self._apply_filter()

        self._update_scan_status(merged, counts, ticked_bytes)

    def _on_scan_error(self, err: str):
        self._watchdog.stop()
        self._scanning = False
        self._scan_worker = None
        self._stop_btn.setEnabled(False)
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText("Scan failed. Nothing was deleted.")
        self._error.set_error(f"Scan error: {err}")
        self._show_page_for_tree()

    def _show_page_for_tree(self) -> None:
        has_rows = self._tree.topLevelItemCount() > 0
        if not has_rows:
            self._empty.setText("No results.\nClick Scan (F5) to measure this tab.")
        self._stack.setCurrentIndex(PAGE_CONTENT if has_rows else PAGE_EMPTY)

    # ── Filter and chips ──

    def _update_chip_texts(self) -> None:
        counts = getattr(self, "_counts", {})
        total = sum(counts.get(key, 0) for key in _RANK)
        in_use = sum(self._was_in_use(row.data(0, Qt.ItemDataRole.UserRole).path)
                     for i in range(self._tree.topLevelItemCount())
                     for row, _ in self._location_rows(self._tree.topLevelItem(i)))
        for key, text in _CHIPS:
            n = total if key == "all" else in_use if key == "in_use" else counts.get(key, 0)
            self._chip_btns[key].setText(f"{text} ({n})")

    def _set_chip(self, key: str) -> None:
        self._chip = key
        for k, btn in self._chip_btns.items():
            btn.setChecked(k == key)
        self._apply_filter()

    def _row_matches(self, node: QTreeWidgetItem, needle: str) -> bool:
        facts = node.data(0, FACTS_ROLE)
        if self._chip == "in_use" and not self._was_in_use(getattr(facts, "path", "")):
            return False
        if self._chip in _RANK and getattr(facts, "safety", "") != self._chip:
            return False
        if not needle:
            return True
        hay = " ".join((node.text(0), getattr(facts, "path", "") or "",
                        getattr(facts, "subtitle", "") or "",
                        getattr(facts, "description", "") or "")).casefold()
        return needle in hay

    def _apply_filter(self, *_args) -> None:
        needle = self._filter.text().strip().casefold()
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            group_text_hit = bool(needle) and self._row_matches_text(group, needle)
            visible = 0
            for row, _holder in self._location_rows(group):
                shown = self._row_matches(row, "" if group_text_hit else needle)
                row.setHidden(not shown)
                visible += shown
            for j in range(group.childCount()):
                holder = group.child(j)
                if holder.data(0, _KIND_ROLE) == "summary":
                    holder.setHidden(not any(not holder.child(k).isHidden()
                                             for k in range(holder.childCount())))
            group.setHidden(visible == 0)
        if self._tree.topLevelItemCount():
            self._refresh_actions()

    @staticmethod
    def _row_matches_text(node: QTreeWidgetItem, needle: str) -> bool:
        facts = node.data(0, FACTS_ROLE)
        hay = " ".join((node.text(0), getattr(facts, "subtitle", "") or "",
                        getattr(facts, "description", "") or "")).casefold()
        return needle in hay

    @staticmethod
    def _location_rows(group: QTreeWidgetItem):
        """(row, holder) for every scanned location under a group, through
        a folded "N files" row when there is one."""
        for j in range(group.childCount()):
            child = group.child(j)
            if child.data(0, _KIND_ROLE) == "summary":
                for k in range(child.childCount()):
                    yield child.child(k), child
            elif child.data(0, Qt.ItemDataRole.UserRole) is not None:
                yield child, group

    # ── Clean ──

    def _get_selected_items(self) -> list:
        """Every ScanItem in the tree, `selected` set from its checkbox.

        Breakdown rows are ScanItems too, so a path can appear twice: once
        as `%TEMP%` and once as `%TEMP%\\wct_p3l` inside it. Anything
        already covered by a selected ancestor is dropped, or the same
        bytes get counted twice in the "about to delete N" confirmation and
        queued for deletion twice. A row hidden by the filter is never
        selected: Clean acts on what can be seen.
        """
        items = []

        def walk(node: QTreeWidgetItem, covered: bool, hidden: bool):
            hidden = hidden or node.isHidden()
            scan_item = node.data(0, Qt.ItemDataRole.UserRole)
            if scan_item is not None:
                selected = (node.checkState(0) == Qt.CheckState.Checked
                            and not covered and not hidden)
                items.append(replace(scan_item, selected=selected))
                covered = covered or selected
            for k in range(node.childCount()):
                walk(node.child(k), covered, hidden)

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i), False, False)
        return items

    def _checked_visible(self) -> tuple[int, int, int]:
        """(count, bytes) of ticked visible rows, and ticked rows hidden."""
        selected = [item for item in self._get_selected_items() if item.selected]
        count, size, hidden = len(selected), sum(item.size for item in selected), 0
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for row, holder in self._location_rows(group):
                if row.checkState(0) != Qt.CheckState.Checked:
                    continue
                if row.isHidden() or holder.isHidden() or group.isHidden():
                    hidden += 1
                    continue
        return count, size, hidden

    def _refresh_actions(self) -> None:
        busy = self._scanning or self._cleaning
        count, size, hidden = self._checked_visible()
        self._clean_btn.setEnabled(count > 0 and not busy)
        self._quick_btn.setEnabled(
            not busy and self._result is not None
            and any(i.safety == "safe" for i in self._result.items))
        tip = (f"Delete the {count} ticked, visible location(s), {cs.format_size(size)} "
               "as last measured (Delete key). Asks first above "
               f"{cs.format_size(CONFIRM_BYTES)}.")
        if hidden:
            tip += f" {hidden} ticked row(s) hidden by the filter are NOT cleaned."
        self._clean_btn.setToolTip(tip)

    def _on_item_changed(self, item, column):
        if column == 0 and not self._tree.signalsBlocked():
            self._refresh_actions()
            if item is self._tree.currentItem():
                self._on_current_changed(item, None)

    def _do_clean(self):
        if self._cleaning or self._scanning or self._result is None:
            return
        selected = self._get_selected_items()
        to_delete = [i for i in selected if i.selected]
        if not to_delete:
            return

        def _on_done(deleted, errors, freed):
            if _alive(self) and worker is self._clean_worker:
                self._on_clean_done((deleted, errors, freed))

        def _on_error(err):
            if _alive(self) and worker is self._clean_worker:
                self._on_clean_error(err)

        worker = csr.run_clean_safe(
            self, selected, stop_wuauserv=self._wu_cache, confirm="size_gated",
            on_done=_on_done, on_error=_on_error)
        if worker is None:
            return  # user declined the confirm -- nothing was disabled yet

        self._cleaning = True
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._status.setText("Cleaning…")
        self._error.clear()
        self._progress.setRange(0, 0)
        self._progress.show()
        self._clean_worker = worker
        self._workers.append(worker)
        def _cancelled():
            if _alive(self) and worker is self._clean_worker:
                self._reset_after_cancel("Clean stopped; partial results are in History.")
        worker.signals.cancelled.connect(_cancelled)

    def _on_clean_done(self, result: tuple):
        deleted, errors, freed = result     # freed is MEASURED, not the scanned size
        self._cleaning = False
        self._clean_worker = None
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        msg = f"Cleaned {deleted} item(s), freed {cs.format_size(freed)} (measured)"
        if errors:
            msg += f" — {errors} could not be deleted"
            self._error.set_error(
                f"{errors} item(s) could not be fully deleted (in use or access "
                "denied). The History tab lists each path and who held it.")
        logger.info("Cleanup complete: deleted %d item(s), freed %s, %d error(s)",
                    deleted, cs.format_size(freed), errors)
        self._status.setText(msg)
        self._last_clean = msg
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)
        self.freed_bytes.emit(freed)
        self._do_scan()

    def _on_clean_error(self, err: str):
        self._cleaning = False
        self._clean_worker = None
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText("The clean did not finish.")
        self._error.set_error(f"Clean error: {err}")
        self._refresh_actions()

    def _do_quick_clean(self):
        """Select only safe items then clean."""
        if self._result is None:
            return
        self._deselect_all()
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            for row, _holder in self._location_rows(self._tree.topLevelItem(i)):
                si = row.data(0, Qt.ItemDataRole.UserRole)
                facts = row.data(0, FACTS_ROLE)
                safe = getattr(facts, "safety", si.safety) == "safe"
                row.setCheckState(0, Qt.CheckState.Checked if safe
                                  else Qt.CheckState.Unchecked)
        self._tree.blockSignals(False)
        self._refresh_actions()
        self._do_clean()

    # ── Selection helpers ──

    def _select_all(self):
        """Tick every visible location rated Safe; Review/Risky are left alone."""
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for row, holder in self._location_rows(group):
                facts = row.data(0, FACTS_ROLE)
                if (getattr(facts, "safety", "") == "safe" and not row.isHidden()
                        and not group.isHidden()):
                    row.setCheckState(0, Qt.CheckState.Checked)
        self._tree.blockSignals(False)
        self._refresh_actions()

    def _deselect_all(self):
        # Location rows do not propagate ticks to their lazy breakdown rows.
        # Explicitly clear descendants too, including collapsed ones.
        def clear(node):
            for index in range(node.childCount()):
                clear(node.child(index))
            if node.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                node.setCheckState(0, Qt.CheckState.Unchecked)

        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            clear(self._tree.topLevelItem(i))
        self._tree.blockSignals(False)
        self._refresh_actions()

    # ── Shortcut routing (never steal keys from the filter box) ──

    def _shortcut_delete(self):
        if QApplication.focusWidget() is self._filter:
            self._filter.del_()
            return
        self._do_clean()

    def _shortcut_select_all(self):
        if QApplication.focusWidget() is self._filter:
            self._filter.selectAll()
            return
        self._select_all()

    def _focus_filter(self):
        self._filter.setFocus()
        self._filter.selectAll()

    def _focus_detail(self):
        if self._tree.currentItem() is not None:
            if self._splitter.sizes()[1] < 260:
                width = self._splitter.width()
                self._splitter.setSizes([max(260, width - 360), 360])
            self._detail.browser.setFocus()

    def _toggle_detail(self):
        sizes = self._splitter.sizes()
        if sizes[1]:
            self._detail_sizes = sizes
            self._splitter.setSizes([sum(sizes), 0])
        else:
            self._splitter.setSizes(self._detail_sizes)

    # ── Detail pane ──

    def _on_current_changed(self, current, _previous):
        if not _alive(self):
            return
        if current is None:
            self._detail.clear()
            return
        facts = current.data(0, FACTS_ROLE)
        if not isinstance(facts, RowFacts):
            self._detail.clear()
            return
        state = current.checkState(0)
        ticked = state == Qt.CheckState.Checked
        if current.data(0, _KIND_ROLE) in ("group", "summary") and not ticked:
            reason = ("Some locations in this group are unticked." if
                      state == Qt.CheckState.PartiallyChecked
                      else "No location in this group is ticked.")
            if UNTICK_REVIEW_BY_DEFAULT:
                reason += " Review and Risky locations start unticked; tick them after reading."
            facts = replace(facts, unselected_reason=reason)
        self._detail.set_row(replace(facts, selected=ticked))
        for button in self._detail.path_buttons:
            button.setToolTip(button.accessibleName())

    # ── Cancel / watchdog ──

    def _cancel_all(self, message: str = None) -> None:
        in_flight = self._scanning or self._cleaning
        self._watchdog.stop()
        self._cancel_breakdowns()
        retire(self._workers)       # held until delivered: see core.worker.retire
        self._scan_worker = None
        self._clean_worker = None
        if in_flight:
            self._reset_after_cancel(message)

    def _current_scanner_label(self) -> str:
        """The scanner the running sweep last reported starting, if any."""
        index = self._progress.value()
        if not (0 < index <= len(self._scan_fns)):
            return ""
        return self._label_of(self._scan_fns[index - 1])

    def _on_scan_watchdog(self) -> None:
        if not self._scanning:
            return
        label = self._current_scanner_label() or "an unknown scanner"
        logger.warning(
            "Cleanup scan timed out after %.0fs, stuck on %s (%d/%d)",
            self.SCAN_WATCHDOG_MS / 1000, label,
            self._progress.value(), len(self._scan_fns))
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._cancel_all(
            message=f"Scan timed out on “{label}” — click Scan to try again")

    def _reset_after_cancel(self, message: str = None) -> None:
        """Put the tab back in a state the user can act on.

        A cancelled Worker emits `cancelled` and never `result` or `error`
        (core/worker.py), so nothing else will ever resolve this scan. Without
        this, switching modules mid-scan left `_scanning` True forever: the
        progress bar stayed up, the status stayed on "Scanning...", and both
        `_do_scan` and `_do_clean` returned early on their in-flight guard —
        a tab that was dead for the life of the process.
        """
        self._watchdog.stop()
        self._scanning = False
        self._cleaning = False
        self._scan_worker = None
        self._clean_worker = None
        # Nothing was measured, so the tab has NOT been scanned: let
        # auto_scan() run again the next time it is activated.
        self._scanned = False
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.hide()
        self._status.setText(
            message or "Scan cancelled — click Scan to run it again")
        self._show_page_for_tree()
        self._refresh_actions()

    # ── Path actions (context menu and detail pane) ──

    def _ctx_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        si = item.data(0, Qt.ItemDataRole.UserRole)
        if not si:
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        open_act = menu.addAction("Open in Explorer")
        open_act.setToolTip("Open this location in File Explorer")
        copy_act = menu.addAction("Copy path")
        copy_act.setToolTip("Copy the full path to the clipboard")
        exclude_act = menu.addAction("Exclude this path from all scans…")
        exclude_act.setToolTip("Never offer or clean this path again, on any Cleanup tab")
        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen == open_act:
            self._open_folder(si.path if si.is_dir else os.path.dirname(si.path))
        elif chosen == copy_act:
            self._copy_path(si.path)
        elif chosen == exclude_act:
            self._exclude_path(si.path)

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

    def _exclude_path(self, path: str) -> None:
        """Never offer `path` (or anything under it) again, on any tab.

        Enforced centrally in `scan_cache.cached_scan`, so this tab does
        not need to touch its own tree beyond re-running the scan to pick
        up the now-smaller result.
        """
        added = exclusions.add_exclusion(path)
        if not added:
            QMessageBox.information(
                self, "Already Excluded",
                f"“{path}” is already on the exclusion list.")
            return
        logger.info("Cleanup: excluded %s from all future scans", path)
        QMessageBox.information(
            self, "Path Excluded",
            f"“{path}” will no longer be offered by any Cleanup tab.\n\n"
            "Manage exclusions from the module's \"Exclusions…\" button.")
        if self._scanned:
            self._do_scan()

    def _build_toolbar(self, layout):
        # Toolbar: actions, then the status right next to what caused it.
        tb = QHBoxLayout()
        tb.setSpacing(6)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setToolTip("Measure every location on this tab again (F5). "
                                  "Read-only: nothing is deleted.")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop after the scanner now running and discard this "
                                  "unfinished scan. Nothing is deleted.")
        self._stop_btn.setEnabled(False)
        self._clean_btn = QPushButton("Clean selected…")
        self._clean_btn.setObjectName("accentButton")
        self._clean_btn.setToolTip(
            "Delete the ticked, visible locations (Delete key). Asks first above "
            f"{cs.format_size(CONFIRM_BYTES)}. Folders keep their root; files in use "
            "are left in place and listed in History.")
        self._clean_btn.setEnabled(False)

        self._more_btn = QToolButton()
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("More: select all safe, deselect all, quick clean, "
                                  "age filter, expand or collapse")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._more_btn)
        menu.setToolTipsVisible(True)
        self._act_sel = menu.addAction("Select all safe", self._select_all)
        self._act_sel.setToolTip("Tick every location rated Safe (Ctrl+A). "
                                 "Review and Risky rows are left as they are.")
        self._act_desel = menu.addAction("Deselect all", self._deselect_all)
        self._act_desel.setToolTip("Untick every location (Ctrl+Shift+A).")
        menu.addSeparator()
        self._quick_btn = menu.addAction("Quick clean: safe only…", self._do_quick_clean)
        self._quick_btn.setToolTip("Tick only the Safe locations, untick everything "
                                   "else, then clean (asks first above "
                                   f"{cs.format_size(CONFIRM_BYTES)}).")
        self._quick_btn.setEnabled(False)
        menu.addSeparator()
        age_box = QWidget(menu)
        age_line = QHBoxLayout(age_box)
        age_line.setContentsMargins(24, 2, 8, 2)
        self._age_lbl = QLabel("Only older than", age_box)
        self._age_spin = QSpinBox(age_box)
        self._age_spin.setRange(0, 3650)
        self._age_spin.setValue(0)
        self._age_spin.setSuffix(" days")
        self._age_spin.setSpecialValueText("any age")
        self._age_spin.setAccessibleName("Only include items older than this many days")
        for w in (age_box, self._age_lbl, self._age_spin):
            w.setToolTip("Only offer files and folders older than this many days "
                         "(any age = no filter). Applies from the next scan.")
        age_line.addWidget(self._age_lbl)
        age_line.addWidget(self._age_spin)
        age_act = QWidgetAction(menu)
        age_act.setDefaultWidget(age_box)
        menu.addAction(age_act)
        menu.addSeparator()
        menu.addAction("Expand all", lambda: self._tree.expandAll()).setToolTip(
            "Open every group (oversized folders are measured only when opened)")
        menu.addAction("Collapse all", lambda: self._tree.collapseAll()).setToolTip(
            "Show one row per location group")
        detail_action = menu.addAction("Show / hide details", self._toggle_detail)
        detail_action.setToolTip("Collapse the explanation pane, or reopen it (F1)")
        self._more_btn.setMenu(menu)

        for w, name in ((self._scan_btn, "Scan this tab"),
                        (self._stop_btn, "Stop scan"),
                        (self._clean_btn, "Clean selected locations"),
                        (self._more_btn, "More selection actions")):
            w.setAccessibleName(name)
            w.setAccessibleDescription(w.toolTip())
            tb.addWidget(w)
        self._status = QLabel("Not scanned yet")
        self._status.setAccessibleName("Scan status")
        self._status.setMinimumWidth(120)
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status.setToolTip("Progress and measured results for this tab")
        set_role(self._status, "muted")
        tb.addSpacing(8)
        tb.addWidget(self._status, 1)
        layout.addLayout(tb)

    def _build_filters(self, layout):
        # Filter line: text plus safety chips with live counts.
        fl = QHBoxLayout()
        fl.setSpacing(4)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by name or path  (Ctrl+F)")
        self._filter.setClearButtonEnabled(True)
        self._filter.setAccessibleName("Filter locations")
        self._filter.setToolTip("Show only locations whose name, path or description "
                                "contains this text. Hidden rows are never cleaned.")
        self._filter.setMaximumWidth(320)
        self._filter.textChanged.connect(self._apply_filter)
        fl.addWidget(self._filter)
        fl.addSpacing(6)
        self._chip_btns: dict[str, QToolButton] = {}
        chip_tips = {
            "all": "Show every location",
            "safe": "Show only locations rated Safe: rebuilt or not needed",
            "caution": "Show only locations to review first; they start unticked",
            "danger": "Show only Risky locations: data that may not come back; "
                      "they start unticked",
            "in_use": "Show locations containing files recorded as in use in the last clean. "
                      "This is historical evidence, not a live lock check.",
        }
        for key, text in _CHIPS:
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.setText(text)
            btn.setToolTip(chip_tips[key])
            btn.setAccessibleName(f"Show {text}")
            btn.setChecked(key == "all")
            btn.clicked.connect(lambda _c=False, k=key: self._set_chip(k))
            self._chip_btns[key] = btn
            fl.addWidget(btn)
        fl.addStretch()
        layout.addLayout(fl)

    def _build_feedback(self, layout):
        self._error = ErrorBanner()
        # ErrorBanner's legacy inline colours are frozen; this host uses QSS
        # roles so its error state follows live dark/light theme switches.
        self._error.setStyleSheet("")
        for child in self._error.findChildren(QWidget):
            child.setStyleSheet("")
            if isinstance(child, QLabel):
                child.setTextFormat(Qt.TextFormat.PlainText)
                child.setWordWrap(True)
                child.setMinimumWidth(0)
                child.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                set_role(child, "statusError")
            elif isinstance(child, QPushButton):
                child.setToolTip("Dismiss this error message")
                child.setAccessibleName("Dismiss scan error")
        layout.addWidget(self._error)

        # Progress bar (determinate over the scanner count)
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setToolTip("Number of location groups measured so far")
        self._progress.hide()
        layout.addWidget(self._progress)

    def _build_results(self, layout):
        # Page 0: tree + detail pane
        self._stack = QStackedWidget()
        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(HEADERS))
        self._tree.setHeaderLabels(["Location", *HEADERS[1:]])
        self._tree.setAccessibleName("Locations found")
        self._tree.setUniformRowHeights(False)
        self._tree.setItemDelegate(SafetyDelegate(self._tree, safety_column=_COL_SAFETY,
                                                  share_column=_COL_SHARE))
        hdr = self._tree.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Interactive)
        self._tree.setColumnWidth(_COL_NAME, 380)
        self._tree.setColumnWidth(_COL_SIZE, 90)
        self._tree.setColumnWidth(_COL_SHARE, 120)
        self._tree.setColumnWidth(_COL_SAFETY, 100)
        self._tree.setColumnWidth(_COL_VERDICT, 220)
        for col, tip in ((_COL_NAME, "Tick a location to clean it. Hover a row for what "
                                     "it is; select it for paths and sources."),
                         (_COL_SIZE, "Size as last measured"),
                         (_COL_SHARE, "Share of everything found on this tab"),
                         (_COL_SAFETY, "Safe, Review or Risky: the more cautious of the "
                                       "catalog and the research"),
                         (_COL_VERDICT, "Whether the data comes back after cleaning"),
                         (_COL_NOTE, "Kept files, other tabs listing the same place")):
            self._tree.headerItem().setToolTip(col, tip)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._ctx_menu)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.currentItemChanged.connect(self._on_current_changed)

        self._detail = DetailPane()
        # A long path label without spaces would otherwise set the pane's
        # minimum, and with it the whole tab's (WP12 fixes the pane itself).
        self._detail.setMinimumWidth(260)
        self._detail.setToolTip("Explanation, paths and research sources for the "
                                "selected row (F1)")
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
        self._splitter.setSizes([860, 440])
        self._detail_sizes = [860, 440]
        self._stack.addWidget(self._splitter)

        # Page 1: empty state
        self._empty = QLabel("Not scanned yet.\nClick Scan (F5) to measure every "
                             "location on this tab. Nothing is deleted by a scan.")
        self._empty.setObjectName("emptyState")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack.addWidget(self._empty)

        # Page 2: loading state
        self._loading = QLabel("Measuring…")
        self._loading.setObjectName("emptyState")
        self._loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading.setWordWrap(True)
        self._stack.addWidget(self._loading)
        self._stack.setCurrentIndex(PAGE_EMPTY)
        layout.addWidget(self._stack, 1)

    def _update_scan_status(self, merged, counts, ticked_bytes):
        n = len(merged.items)
        logger.info("Cleanup scan complete: %d item(s) (%d safe), %s",
                    n, counts["safe"], cs.format_size(merged.total_size))
        if n == 0:
            self._empty.setText("Nothing to clean here right now.\n"
                                "Click Scan (F5) to look again.")
            self._stack.setCurrentIndex(PAGE_EMPTY)
            status = "Nothing found"
        else:
            self._stack.setCurrentIndex(PAGE_CONTENT)
            review = counts["caution"] + counts["danger"]
            parts = [f"{n} location(s)", cs.format_size(merged.total_size),
                     f"{counts['safe']} safe"]
            if review:
                parts.append(f"{review} to review")
            parts.append(f"{cs.format_size(ticked_bytes)} ticked")
            status = " · ".join(parts)
        if self._last_clean:
            status = f"{self._last_clean}. Now: {status}"
            self._last_clean = ""
        self._status.setText(status)
        self._refresh_actions()
