"""Task Manager's Startup apps tab, as a read-only combined list.

Task Manager's Startup page splits apps, scheduled tasks and services into
three collapsible groups. This tab does not know Task Manager's grouping, so
it presents the same inventory as one table whose Source column says where
each row came from.

What is deliberately absent: the "Startup impact" column Task Manager shows.
Windows grades an app High, Medium or Low by measuring how long it delays
boot, using traces it records over several starts. This tool reads none of
that telemetry, so a grade here would be invented -- the tab says so in a
line above the table instead of showing one.
"""
import logging
from typing import List, Tuple

from core.table_ui import set_role
from ui.table_style import apply_table_style
from ui.chip_bar import ChipBar
from .glance_support import summary, unreadable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core.worker import COMWorker, retire

from modules.startup_manager.startup_reader import (StartupEntry,
                                                    get_browser_extensions,
                                                    get_machine_registry_entries,
                                                    get_registry_entries,
                                                    get_scheduled_task_entries,
                                                    get_service_entries,
                                                    get_startup_folder_entries)

logger = logging.getLogger(__name__)

_HEADERS = ("Name", "Command / path", "Status", "Source", "Details")

_SOURCE_LABELS = {
    "registry_run": "Registry (user)",
    "registry_run_machine": "Registry (machine)",
    "startup_folder": "Startup folder",
    "task": "Scheduled task",
    "service": "Service",
    "browser_ext": "Browser extension",
}

_SOURCES = (
    ("registry_run", get_registry_entries),
    ("registry_run_machine", get_machine_registry_entries),
    ("startup_folder", get_startup_folder_entries),
    ("task", get_scheduled_task_entries),
    ("service", get_service_entries),
    ("browser_ext", get_browser_extensions),
)

# The order rows are presented in, so the table reads the way Task Manager's
# Startup page does: Run keys first, then folders, tasks, services, browsers.
_SOURCE_ORDER = {key: index for index, key in enumerate(_SOURCE_LABELS)}

_HONESTY_NOTE = (
    "Windows grades an app's \"startup impact\" High, Medium or Low from "
    "boot traces it records over your last few starts. This tool does not "
    "read those traces, so it cannot know an app's real impact -- a "
    "High/Medium/Low grade shown here would be made up, and there is none.")


class StartupTab(QWidget):
    """Everything that runs at startup, in one table. Owns its workers.

    The scheduled-task reader drives COM, so the scan runs on a `COMWorker`
    (which calls `pythoncom.CoInitialize` on its thread) rather than a plain
    `Worker`. Every getter swallows its own failures and returns a list, and
    the ones a machine can refuse are still reported in the status line
    rather than hidden.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._app = None
        self._busy = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._entries = []
        self._glance = summary(self, layout, [
            ("enabled", "Enabled", "Enabled items in the sources successfully read; select to filter."),
            ("impact", "High impact", "Windows boot-trace impact grades are not read by this tool."),
            ("unsigned", "Unsigned", "Startup executable signatures are not verified by this reader."),
            ("recent", "Added recently", "These startup sources do not expose a reliable date added.")])
        self._glance["enabled"].clicked.connect(lambda: self._chips.chips["enabled"].click())
        note = QLabel(_HONESTY_NOTE)
        note.setWordWrap(True)
        set_role(note, "muted")
        layout.addWidget(note)

        top = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        top.addStretch(1)
        layout.addLayout(top)

        self._chips = ChipBar([("all", "All", 0), ("enabled", "Enabled", 0),
                               ("disabled", "Disabled", 0)], parent=self)
        self._chips.selection_changed.connect(lambda _keys: self._filter_entries())
        layout.addWidget(self._chips)
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        header = self._table.horizontalHeader()
        # The command column is prose and takes the free width; the rest hug
        # their content so the table always fills the pane.
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        apply_table_style(self._table)
        layout.addWidget(self._table, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

    def set_app(self, app) -> None:
        self._app = app

    def start(self) -> None:
        """First-load trigger. The module's host calls this on activate."""
        self.refresh()

    def stop(self) -> None:
        self.cancel_all()

    def cancel_all(self) -> None:
        retire(self._workers)
        self._busy = False

    # ---- reading --------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            self._apply(self._read())
            return
        self._busy = True
        self.status.setText("Scanning startup items…")
        worker = COMWorker(lambda _worker: self._read())
        worker.signals.result.connect(self._apply)
        worker.signals.error.connect(self._failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        pool.start(worker)

    def _read(self) -> Tuple[List[StartupEntry], List[str]]:
        """Call every source and combine. Runs off the main thread.

        A source that raises (COM refused, win32 modules absent) is logged,
        named in `problems` and left out -- the rest of the machine still
        shows, and the status line says what could not be read.
        """
        entries: List[StartupEntry] = []
        problems: List[str] = []
        for source, getter in _SOURCES:
            try:
                rows = getter()
            except Exception as exc:
                label = _SOURCE_LABELS.get(source, source)
                logger.warning("Startup scan source %s failed: %s", source, exc)
                problems.append(f"{label}: {exc}")
                continue
            entries.extend(rows)
        entries.sort(
            key=lambda entry: (_SOURCE_ORDER.get(entry.source, 99),
                               entry.name.lower()))
        return entries, problems

    def _apply(self, result) -> None:
        self._busy = False
        entries, problems = result
        self._entries = entries
        enabled = sum(e.enabled for e in entries)
        self._glance["enabled"].set(enabled if entries or not problems else None,
                                   "in readable sources", reason="; ".join(problems))
        self._glance["impact"].set_unreadable("Windows boot-trace impact grades are not read")
        self._glance["unsigned"].set_unreadable("Startup signatures are not checked")
        self._glance["recent"].set_unreadable("Reliable date added is not available")
        self._chips.set_counts({"all": len(entries), "enabled": enabled, "disabled": len(entries) - enabled})
        self._filter_entries()

        parts = []
        if entries:
            parts.append(f"{len(entries):,} startup items")
        else:
            parts.append("No startup items found")
        if problems:
            parts.append(f"{len(problems)} source(s) could not be read")
        self.status.setText("   ·   ".join(parts))

    def _filter_entries(self):
        key = next(iter(self._chips.selected()), "all")
        self._populate([e for e in self._entries if key == "all" or e.enabled == (key == "enabled")])

    def _failed(self, message) -> None:
        self._busy = False
        unreadable(self._glance, str(message))
        logger.error("Startup scan failed: %s", message)
        self.status.setText(f"Could not scan startup items: {message}")

    def _on_cancelled(self) -> None:
        self._busy = False

    def _populate(self, entries) -> None:
        self._table.setRowCount(len(entries))
        for index, entry in enumerate(entries):
            command = entry.command or "—"
            details = entry.extra or "—"
            status = "Enabled" if entry.enabled else "Disabled"
            source = _SOURCE_LABELS.get(entry.source, entry.source)
            for column, text in enumerate(
                    (entry.name, command, status, source, details)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    int(Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter))
                self._table.setItem(index, column, item)
            self._table.item(index, 0).setToolTip(
                f"{source} · {command}")
