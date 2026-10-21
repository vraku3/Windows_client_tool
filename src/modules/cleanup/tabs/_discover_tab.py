"""Read-only review of caches not covered by the cleanup catalog.

Nothing on this tab deletes anything: it lists, explains and copies. The
presentation helpers at the top are Qt-free so they test headless; the only
filesystem call they make is listing a candidate's PARENT folder (names only)
to decide whether generic Chromium research applies, and that runs on the
worker, never on the UI thread.
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QHeaderView, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.long_op_pool import get_long_op_pool
from core.table_ui import NumericSortItem, set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup.cleanup_scanner import format_size
from modules.cleanup.cleanup_scanner import discover as _discover
from modules.cleanup.cleanup_scanner._common import ScanItem
from modules.cleanup.cleanup_scanner.discover import find_uncovered_caches
from modules.cleanup.components.detail_pane import DetailPane
from modules.cleanup.components.item_rows import RowFacts, knowledge_api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Qt-free presentation
# ---------------------------------------------------------------------------

#: What kind of folder each cache-name token in discover.CACHE_NAMES names.
TOKEN_KINDS = {
    "cache": "generic cache",
    "cache_data": "cache data",
    "code cache": "script cache (Chromium)",
    "gpucache": "GPU cache (Chromium)",
    "dawncache": "WebGPU cache (Chromium)",
    "dawngraphitecache": "WebGPU cache (Chromium)",
    "dawnwebgpucache": "WebGPU cache (Chromium)",
    "grshadercache": "Skia shaders (Chromium)",
    "shadercache": "shader cache",
    "shader cache": "shader cache",
    "dxcache": "DirectX shaders",
    "glcache": "OpenGL shaders",
    "vkcache": "Vulkan shaders",
    "nv_cache": "NVIDIA shaders",
    "d3dscache": "Direct3D shaders",
    "crashpad": "crash reporter",
    "crashdumps": "crash dumps",
    "crash reports": "crash reports",
    "logs": "log folder",
    "cacheddata": "cached data",
    "cachedextensionvsixs": "cached extensions",
    "cachedprofilesdata": "cached profile data",
    "webcache": "web cache",
    "htmlcache": "HTML cache",
}

#: Names Chromium alone uses: generic research applies from the name alone.
_CHROMIUM_ONLY = {
    "gpucache": "browser:GPUCache",
    "code cache": "browser:Code Cache",
    "dawncache": "browser:DawnCache",
    "grshadercache": "browser:GrShaderCache",
}
#: Common names: Chromium research applies only inside a Chromium profile.
_CHROMIUM_IF_PROFILE = {
    "cache": "browser:Cache",
    "shadercache": "browser:ShaderCache",
}
#: Sibling names that mark a Chromium/Electron profile folder (names only;
#: none is ever opened).
CHROMIUM_MARKERS = frozenset({
    "code cache", "gpucache", "local storage", "session storage", "indexeddb",
    "blob_storage", "dawncache", "dawngraphitecache", "dawnwebgpucache",
    "grshadercache", "shared_proto_db", "service worker", "preferences", "local state",
})
#: Path parts that say nothing about who owns a folder.
_GENERIC_PARTS = frozenset({
    "user data", "default", "local", "roaming", "locallow", "localcache", "localstate",
    "data", "app", "ac", "temp", "tmp",
})

_ROOT_LABELS = {
    "%LOCALAPPDATA%": "Local AppData",
    "%APPDATA%": "Roaming AppData",
    "%PROGRAMDATA%": "ProgramData",
    r"%USERPROFILE%\.cache": "user .cache",
    r"%USERPROFILE%\AppData\LocalLow": "LocalLow AppData",
}


def plural(count: int, singular: str, many: Optional[str] = None) -> str:
    """'1 folder', '2 folders' -- D9 ("Found 1 folders")."""
    word = singular if count == 1 else (many or singular + "s")
    return f"{count} {word}"


def expanded_roots(roots: Iterable[str] = None) -> List[Tuple[str, str]]:
    """(raw, expanded) pairs, longest expansion first so nested roots win."""
    pairs = []
    for raw in (_discover.ROOTS if roots is None else roots):
        expanded = os.path.normpath(os.path.expandvars(os.fspath(raw)))
        if "%" in expanded:
            continue  # the variable is not set on this machine
        pairs.append((os.fspath(raw), expanded))
    return sorted(pairs, key=lambda pair: len(pair[1]), reverse=True)


def _root_of(path: str, roots: Sequence[Tuple[str, str]]):
    key = os.path.normcase(os.path.normpath(path))
    for raw, expanded in roots:
        base = os.path.normcase(expanded).rstrip("\\/")
        if key == base or key.startswith(base + os.sep):
            rest = os.path.normpath(path)[len(base):].lstrip("\\/")
            return raw, expanded, [part for part in re.split(r"[\\/]", rest) if part]
    return None, None, []


def guess_owner(path: str, roots: Sequence[Tuple[str, str]] = None) -> str:
    """Nearest meaningful folder under AppData/ProgramData. A guess, labelled so."""
    roots = expanded_roots() if roots is None else roots
    _raw, _expanded, parts = _root_of(path, roots)
    parts = parts[:-1]  # the cache folder itself names no owner
    if not parts:
        return ""
    first = parts[0]
    if first.lower() == "packages" and len(parts) > 1:
        return parts[1].split("_")[0]  # package family without the publisher hash
    if first.lower() == "programs" and len(parts) > 1:
        return parts[1]
    if len(parts) > 1:
        second = parts[1]
        low = second.lower()
        if (low not in _GENERIC_PARTS and low not in TOKEN_KINDS
                and not low.startswith("profile ")):
            return f"{first} › {second}"
    return first


def root_label(path: str, roots: Sequence[Tuple[str, str]] = None) -> str:
    raw, _expanded, _parts = _root_of(path, expanded_roots() if roots is None else roots)
    return _ROOT_LABELS.get(raw, raw or "")


def why_listed(name: str) -> str:
    """The matched cache-name token and what such folders usually are."""
    return f"{name}: {TOKEN_KINDS.get(name.lower(), 'cache-like name')}"


def research_key(name: str, siblings: Iterable[str] = ()) -> str:
    """The generic KB key for this folder TYPE, or '' when none honestly applies."""
    low = name.lower()
    if low in _CHROMIUM_ONLY:
        return _CHROMIUM_ONLY[low]
    if low in _CHROMIUM_IF_PROFILE:
        others = {s.lower() for s in siblings} - {low}
        if others & CHROMIUM_MARKERS:
            return _CHROMIUM_IF_PROFILE[low]
    return ""


def sibling_names(path: str, cancelled=lambda: False) -> List[str]:
    """Names next to `path` (read-only listing; an error answers 'none')."""
    parent = os.path.dirname(path)
    if not parent or cancelled():
        return []
    try:
        return os.listdir(parent)
    except OSError as exc:
        logger.debug("Discover: cannot list %s: %s", parent, exc)
        return []


@dataclass(frozen=True)
class DiscoverRow:
    path: str
    size: int
    name: str
    why: str
    owner: str
    location: str
    research_key: str = ""

    @property
    def researched(self) -> bool:
        return bool(self.research_key)

    @property
    def label(self) -> str:
        return f"{self.owner} – {self.name}" if self.owner else self.name


def annotate(candidates, roots=None, cancelled=lambda: False,
             siblings=sibling_names) -> List[DiscoverRow]:
    pairs = expanded_roots(roots)
    rows = []
    for candidate in candidates:
        if cancelled():
            break
        names = siblings(candidate.path, cancelled) if candidate.name.lower() in _CHROMIUM_IF_PROFILE else []
        rows.append(DiscoverRow(
            path=candidate.path, size=candidate.size, name=candidate.name,
            why=why_listed(candidate.name), owner=guess_owner(candidate.path, pairs),
            location=root_label(candidate.path, pairs),
            research_key=research_key(candidate.name, names)))
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "folder"


def portable_path(path: str, roots: Sequence[Tuple[str, str]] = None) -> str:
    """The path with its root written as the environment variable."""
    raw, _expanded, parts = _root_of(path, expanded_roots() if roots is None else roots)
    if raw is None:
        return path
    return "\\".join([raw] + parts)


def catalog_stub(row: DiscoverRow, roots=None, today: Optional[date] = None) -> str:
    """A catalog JSON entry to REVIEW, never one that runs.

    It carries a disabled_reason, so pasting it unread into a definitions file
    adds a documented, disabled entry rather than a new delete target.
    """
    owner = row.owner.replace("›", " ").strip()
    stamp = (today or date.today()).isoformat()
    stub = {
        "id": f"{_slug(owner)}_{_slug(row.name)}" if owner else _slug(row.name),
        "label": f"{owner} {row.name}".strip(),
        "paths": [portable_path(row.path, expanded_roots(roots))],
        "safety": "caution",
        "description": (f"Found by Discover on {stamp}: a folder named '{row.name}'"
                        + (f" under {owner}" if owner else "") + ". Not reviewed yet."),
        "disabled_reason": (
            "Stub copied from Discover, not reviewed: confirm it is a cache leaf (not the "
            "app's data folder, a sign-in store, IndexedDB/Local Storage/Network) and add "
            "research for it before removing this line."),
    }
    return json.dumps(stub, indent=2, ensure_ascii=False)


def _entry(row: DiscoverRow):
    api = knowledge_api()
    return api.entry_for(row.research_key) if api and row.research_key else None


def research_text(row: DiscoverRow, entry) -> str:
    if entry is None:
        return "Not yet researched"
    return "Chromium research"


def tooltip_for(row: DiscoverRow, entry) -> str:
    api = knowledge_api()
    owner = escape(row.owner or "unknown")
    lines = (f"<p><b>Why listed:</b> {escape(row.why)}. Not in the cleanup list.</p>"
             f"<p><b>Owner (guess):</b> {owner} · {escape(row.location or 'other location')}</p>")
    if api is not None:
        html = api.tooltip_html(entry, row.label, "review", format_size(row.size))
    else:
        html = f"<b>{escape(row.label)}</b><p>Not yet researched</p>"
    if entry is not None:
        lines += ("<p><i>This research is about the folder type in Chromium and Electron apps, "
                  "not about this app.</i></p>")
    return "<qt>" + html + lines + "<p>Discover never deletes.</p></qt>"


def detail_markup(row: DiscoverRow, entry) -> str:
    out = [f"<h2>{escape(row.label)}</h2>",
           f"<p>{escape(format_size(row.size))} · not in the cleanup list · review only</p>",
           "<h3>Why it is listed</h3>",
           f"<p>The folder name matched: {escape(row.why)}. Its path is not covered by any enabled "
           "cleanup entry, and it is not inside a folder a disabled entry rejected.</p>",
           "<h3>Who owns it (guess)</h3>",
           f"<p>{escape(row.owner or 'Unknown')} "
           f"({escape(row.location or 'outside the usual app data folders')}). "
           "The owner is read from the path, not confirmed.</p>"]
    if entry is not None:
        out.append(f"<h3>Research on Chromium “{escape(row.name)}” folders</h3>")
        out.append("<p><i>About this folder type in Chromium and Electron apps, not about "
                   "this particular app.</i></p>")
        for title, value in (("What", entry.what), ("Used for", entry.used_for),
                             ("Risk", entry.risk), ("Before cleaning", entry.before_cleaning)):
            if value:
                out.append(f"<p><b>{title}:</b> {escape(value)}</p>")
        out.append("<p><b>Comes back:</b> "
                   + ("Yes, automatically" if entry.regenerates
                      else "No. Deleted data does not come back") + "</p>")
    else:
        out.append("<h3>Research</h3><p>Not yet researched. Nothing is known about this "
                   "folder beyond its name, path and size.</p>")
    out.append("<h3>What this tab will do</h3><p>Nothing. Discover never deletes. Clear it "
               "from the app that owns it, or copy a catalog stub and review it before "
               "adding it to the cleanup list.</p>")
    out.append(f"<h3>Path</h3><p>{escape(row.path)}</p>")
    if entry is not None and entry.sources:
        out.append("<h3>Sources</h3>" + "".join(
            f'<p><a href="{escape(url, quote=True)}">{escape(urlsplit(url).netloc)}</a></p>'
            for url in entry.sources))
    return "".join(out)


# ---------------------------------------------------------------------------
# Qt
# ---------------------------------------------------------------------------

_COLUMNS = ("Size", "Folder", "Why listed", "Owner (guess)", "Research")
_HEADER_TIPS = (
    "Measured size of the folder (links and protected subfolders are not followed).",
    "Full path of the folder.",
    "Which cache-like folder name matched.",
    "The app the folder probably belongs to, read from its path. A guess.",
    "Whether research about this kind of folder exists.",
)
_ROW_ROLE = Qt.ItemDataRole.UserRole


class _DiscoverTab(QWidget):
    freed_bytes = pyqtSignal(int)  # never emitted: Discover deletes nothing

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._pool = get_long_op_pool()
        self._rows: List[DiscoverRow] = []
        layout = QVBoxLayout(self)
        description = QLabel(
            "Folders that look like caches but are not in the cleanup list yet. "
            "Nothing here is deleted: review each one; most are best cleared "
            "from the app that owns them.")
        description.setWordWrap(True)
        set_role(description, "muted")
        layout.addWidget(description)

        row = QHBoxLayout()
        minimum_label = QLabel("Minimum size")
        row.addWidget(minimum_label)
        self._minimum = QSpinBox()
        self._minimum.setRange(1, 1_000_000)
        self._minimum.setSuffix(" MB")
        self._minimum.setValue(20)
        self._minimum.setToolTip("Folders smaller than this are not listed.")
        self._minimum.setAccessibleName("Minimum folder size in megabytes")
        minimum_label.setBuddy(self._minimum)
        row.addWidget(self._minimum)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setToolTip(
            "Look through AppData, ProgramData and .cache for cache-named folders the cleanup "
            "list does not cover. Read-only; takes about 10-20 seconds.")
        self._scan_btn.setAccessibleName("Scan for uncovered caches")
        self._scan_btn.clicked.connect(self._scan)
        row.addWidget(self._scan_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop the scan. Nothing was changed either way.")
        self._stop_btn.setAccessibleName("Stop the discover scan")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._cancel_all)
        row.addWidget(self._stop_btn)
        self._status = QLabel("Click Scan to discover uncovered caches.")
        self._status.setAccessibleName("Discover status")
        row.addWidget(self._status, 1)
        layout.addLayout(row)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        for column, tip in enumerate(_HEADER_TIPS):
            self._table.horizontalHeaderItem(column).setToolTip(tip)
        self._table.setAccessibleName("Uncovered cache folders")
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.setWordWrap(False)  # wrapping at a backslash elided paths to 'C:...'
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # Fixed, user-resizable widths: content-sized text columns squeezed the
        # path to three characters in the first render.
        for column, width in ((2, 220), (3, 130), (4, 140)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, width)
        header.setMinimumSectionSize(60)
        self._table.itemSelectionChanged.connect(self._selection_changed)

        self._detail = DetailPane()
        self._detail.open_folder.connect(self._open_path)
        self._detail.copy_path.connect(self._copy_text)
        self._detail.browser.setPlainText(
            "Select a folder to see why it is listed, who probably owns it and what is known "
            "about it.")

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(self._detail)
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([800, 420])

        self._empty = QLabel(
            "No scan yet. Click Scan to look for cache folders the cleanup list does not cover.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        set_role(self._empty, "emptyState")
        self._stack = QStackedWidget()
        self._stack.addWidget(self._splitter)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentIndex(1)
        layout.addWidget(self._stack, 1)

        actions = QHBoxLayout()
        self._action_btns = []
        for text, tip, callback in [
                ("Open folder", "Open the selected folder in Explorer.", self._open_folder),
                ("Copy path", "Copy the selected folder's path.", self._copy_path),
                ("Copy as catalog JSON stub",
                 "Copy a DISABLED catalog entry for this folder to the clipboard, to review "
                 "and research before it is ever enabled. Nothing is written to disk.",
                 self._copy_stub)]:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setAccessibleName(text)
            button.setEnabled(False)
            button.clicked.connect(callback)
            actions.addWidget(button)
            self._action_btns.append(button)
        actions.addStretch()
        layout.addLayout(actions)

    # -- scanning ----------------------------------------------------------

    def _set_busy(self, busy: bool):
        self._scan_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)

    def _scan(self):
        if not widget_is_valid(self) or self._worker is not None:
            return
        minimum = self._minimum.value()
        self._set_busy(True)
        set_role(self._status, "")
        self._status.setText("Scanning for uncovered caches...")

        def run(worker):
            result = find_uncovered_caches(min_bytes=minimum * 2**20,
                                           cancelled=lambda: worker.is_cancelled)
            return result, annotate(result.candidates, cancelled=lambda: worker.is_cancelled)

        worker = Worker(run)

        def done(payload):
            if not widget_is_valid(self) or self._worker is not worker:
                return
            result, rows = payload
            self._worker = None
            self._set_busy(False)
            self._show_rows(result, minimum, rows)

        def error(reason):
            if not widget_is_valid(self) or self._worker is not worker:
                return
            self._worker = None
            self._set_busy(False)
            set_role(self._status, "statusError")
            self._status.setText(f"Scan failed: {reason}")

        def cancelled():
            if not widget_is_valid(self) or self._worker is not worker:
                return
            self._worker = None
            self._set_busy(False)
            self._status.setText("Scan cancelled.")

        worker.signals.result.connect(done)
        worker.signals.error.connect(error)
        worker.signals.cancelled.connect(cancelled)
        self._worker = worker
        self._pool.start(worker)

    def _show_rows(self, result, minimum, rows=None):
        if not widget_is_valid(self):
            return
        if rows is None:
            rows = annotate(result.candidates)
        self._rows = list(rows)
        self._detail.clear()
        self._table.setSortingEnabled(False)  # filling a sorting table scrambles rows
        self._table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            entry = _entry(row)
            tip = tooltip_for(row, entry)
            cells = [
                NumericSortItem(format_size(row.size), row.size),
                QTableWidgetItem(row.path),
                QTableWidgetItem(row.why),
                QTableWidgetItem(row.owner or "Unknown"),
                QTableWidgetItem(research_text(row, entry)),
            ]
            cells[0].setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for column, cell in enumerate(cells):
                cell.setData(_ROW_ROLE, row)
                cell.setToolTip(tip)
                self._table.setItem(index, column, cell)
        self._table.setSortingEnabled(True)
        self._table.sortItems(0, Qt.SortOrder.DescendingOrder)
        if rows:
            total = format_size(sum(row.size for row in rows))
            status = (f"Found {plural(len(rows), 'folder')}, {total}, not covered by the "
                      "cleanup list.")
            self._stack.setCurrentIndex(0)
        else:
            status = f"Nothing uncovered above {minimum} MB."
            self._empty.setText(
                f"Nothing uncovered above {minimum} MB: every cache-named folder that size is "
                "already in the cleanup list or was reviewed and left out on purpose.")
            self._stack.setCurrentIndex(1)
        if result.unreadable:
            # Wording pinned verbatim by tests/test_cleanup_discover.py (not this
            # package's file); singularise it when that test is updated.
            status += (f" {result.unreadable} protected folders were skipped"
                       " (normal: mostly Windows' own).")
        set_role(self._status, "")
        self._status.setText(status)
        self._status.setToolTip(status)
        self._selection_changed()

    # -- selection and actions --------------------------------------------

    def _selected_row(self) -> Optional[DiscoverRow]:
        if not widget_is_valid(self):
            return None
        item = self._table.item(self._table.currentRow(), 1)
        return item.data(_ROW_ROLE) if item is not None else None

    def _selected_path(self):
        row = self._selected_row()
        return row.path if row is not None else None

    def _selection_changed(self):
        if not widget_is_valid(self):
            return
        row = self._selected_row() if self._table.selectedItems() else None
        for button in self._action_btns:
            button.setEnabled(row is not None)
        if row is None:
            self._detail.clear()
            return
        entry = _entry(row)
        facts = RowFacts.from_item(
            ScanItem(path=row.path, size=row.size, is_dir=True, selected=False,
                     safety="caution"),
            row.label, catalog_id=row.research_key, description=row.why)
        self._detail.set_row(facts)
        for button in self._detail.path_buttons:
            # Exclusions hide items from CLEANING; nothing here is cleaned.
            if button.text().startswith("Exclude"):
                button.hide()
        self._detail.browser.setHtml(detail_markup(row, entry))

    def _open_path(self, path):
        if not widget_is_valid(self) or not path:
            return
        try:
            os.startfile(path, "explore")
        except OSError as exc:
            set_role(self._status, "statusError")
            self._status.setText(f"Open folder failed: {exc}")

    def _copy_text(self, text):
        if widget_is_valid(self) and text:
            QApplication.clipboard().setText(text)

    def _open_folder(self):
        self._open_path(self._selected_path())

    def _copy_path(self):
        self._copy_text(self._selected_path())

    def _copy_stub(self):
        if not widget_is_valid(self):
            return
        row = self._selected_row()
        if row is None:
            return
        self._copy_text(catalog_stub(row))
        self._status.setText("Catalog stub copied (disabled until reviewed). Nothing was written.")

    def _cancel_all(self):
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
            if widget_is_valid(self):
                self._set_busy(False)
                self._status.setText("Scan cancelled.")
