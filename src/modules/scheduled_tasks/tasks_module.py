import logging
import os
import subprocess
from typing import Callable, List, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import center_header, centered_item, set_role
from core.worker import COMWorker, Worker
from modules.dashboard.tab_base import make_chips, set_chip_counts
from modules.scheduled_tasks import task_actions, task_view
from modules.scheduled_tasks.tasks_reader import (
    ALL_FOLDERS, TaskFolder, TaskInfo, get_folder_tree, get_tasks_in_folder,
)

logger = logging.getLogger(__name__)

TASK_COLS = ["Name", "Folder", "State", "Last Run", "Last Result", "Next Run",
             "Runs as", "Triggers", "Author"]
_COL_STATE, _COL_RESULT = 2, 4


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


class TasksModule(BaseModule):
    name = "Scheduled Tasks"
    icon = "📅"
    description = "View, triage and manage scheduled tasks"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._tasks: List[TaskInfo] = []
        self._shown: List[TaskInfo] = []
        self._filter_key = "all"
        self._folder_path = ALL_FOLDERS
        self._tasks_loaded = False

    # ------------------------------------------------------------------
    # BaseModule lifecycle
    # ------------------------------------------------------------------
    def on_start(self, app) -> None:
        self.app = app

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        if self._widget is None or not _alive(self._widget):
            return
        self._load_tasks()

    def on_activate(self) -> None:
        if self._widget is not None and not self._tasks_loaded:
            self._tasks_loaded = True
            self._load_folders()
            self._load_tasks()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()
        self._tasks_loaded = bool(self._tasks)

    def on_stop(self) -> None:
        self.cancel_all_workers()

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------
    def create_widget(self) -> QWidget:
        w = self._widget = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(self._build_toolbar())
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        lay.addWidget(self._progress)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_folder_tree())
        splitter.addWidget(self._build_right_side())
        splitter.setSizes([220, 900])
        lay.addWidget(splitter, 1)
        self._sync_buttons()
        return w

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._enable_btn = QPushButton("Enable")
        self._disable_btn = QPushButton("Disable")
        self._run_btn = QPushButton("Run Now")
        self._delete_btn = QPushButton("Delete")
        self._export_btn = QPushButton("Export XML")
        self._copy_btn = QPushButton("Copy details")
        self._sig_btn = QPushButton("Verify program")
        self._sig_btn.setToolTip("Check the Authenticode signature of the task's program")
        taskschd = QPushButton("Task Scheduler")
        self._status_lbl = QLabel("Loading...")
        set_role(self._status_lbl, "muted")
        for b in (self._refresh_btn, self._enable_btn, self._disable_btn, self._run_btn,
                  self._delete_btn, self._export_btn, self._copy_btn, self._sig_btn, taskschd):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self._status_lbl)
        self._refresh_btn.clicked.connect(self._refresh_all)
        self._enable_btn.clicked.connect(lambda: self._change_enabled(True))
        self._disable_btn.clicked.connect(lambda: self._change_enabled(False))
        self._run_btn.clicked.connect(self._run_selected)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._export_btn.clicked.connect(self._export_selected)
        self._copy_btn.clicked.connect(self._copy_selected)
        self._sig_btn.clicked.connect(self._verify_selected)
        taskschd.clicked.connect(lambda: subprocess.Popen(
            ["taskschd.msc"], shell=True, creationflags=subprocess.CREATE_NO_WINDOW))
        return bar

    def _build_folder_tree(self) -> QTreeWidget:
        self._folder_tree = QTreeWidget()
        self._folder_tree.setHeaderLabel("Task Folders")
        self._folder_tree.setMinimumWidth(180)
        self._folder_tree.itemClicked.connect(self._on_folder_clicked)
        return self._folder_tree

    def _build_right_side(self) -> QWidget:
        right = QWidget()
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        chip_row, self._chips = make_chips(right, task_view.FILTERS, self._pick_filter)
        lay.addLayout(chip_row)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search name, folder, author, program, arguments, account...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._render)
        lay.addWidget(self._search)
        self._table = QTableWidget(0, len(TASK_COLS))
        self._table.setHorizontalHeaderLabels(TASK_COLS)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for col, width in ((0, 230), (1, 170), (6, 150), (7, 150)):
            self._table.setColumnWidth(col, width)
        self._table.horizontalHeader().setStretchLastSection(True)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.itemSelectionChanged.connect(self._on_task_selected)
        lay.addWidget(self._table, 1)
        lay.addWidget(self._build_detail_tabs())
        return right

    def _build_detail_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setMaximumHeight(230)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        set_role(self._detail, "mono")
        self._detail.setPlaceholderText("Select a task to see what it runs, as whom, and why it failed.")
        self._xml_view = QPlainTextEdit()
        self._xml_view.setReadOnly(True)
        tabs.addTab(self._detail, "Details")
        tabs.addTab(self._xml_view, "XML definition")
        return tabs

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _start(self, worker) -> None:
        self._workers.append(worker)
        pool = getattr(getattr(self, "app", None), "thread_pool", None)
        (pool or QThreadPool.globalInstance()).start(worker)

    def _refresh_all(self) -> None:
        self._load_folders()
        self._load_tasks()

    def _load_folders(self) -> None:
        worker = COMWorker(lambda _w: get_folder_tree())
        worker.signals.result.connect(self._fill_folders)
        worker.signals.error.connect(self._on_error)
        self._start(worker)

    def _fill_folders(self, root: TaskFolder) -> None:
        if not _alive(self._widget):
            return
        self._folder_tree.clear()
        everything = QTreeWidgetItem(self._folder_tree, ["All tasks (every folder)"])
        everything.setData(0, Qt.ItemDataRole.UserRole, ALL_FOLDERS)

        def add(parent, tf: TaskFolder):
            item = QTreeWidgetItem(parent, [tf.name])
            item.setData(0, Qt.ItemDataRole.UserRole, tf.path)
            for sub in tf.subfolders:
                add(item, sub)
            return item

        add(self._folder_tree, root).setExpanded(True)
        self._select_folder_item(everything)

    def _select_folder_item(self, item) -> None:
        self._folder_tree.blockSignals(True)
        self._folder_tree.setCurrentItem(item)
        self._folder_tree.blockSignals(False)

    def _on_folder_clicked(self, item, _col) -> None:
        self._folder_path = item.data(0, Qt.ItemDataRole.UserRole) or "\\"
        self._load_tasks()

    def _load_tasks(self) -> None:
        path = self._folder_path
        self._progress.show()
        self._status_lbl.setText("Loading tasks...")
        worker = COMWorker(lambda _w: get_tasks_in_folder(path))
        worker.signals.result.connect(self._on_tasks)
        worker.signals.error.connect(self._on_error)
        self._start(worker)

    def _on_error(self, err: str) -> None:
        if not _alive(self._widget):
            return
        self._progress.hide()
        self._status_lbl.setText(f"Error: {err}")

    def _on_tasks(self, tasks: List[TaskInfo]) -> None:
        if not _alive(self._widget):
            return
        self._progress.hide()
        self._tasks = tasks
        set_chip_counts(self._chips, task_view.filter_counts(tasks))
        self._render()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _pick_filter(self, key: str) -> None:
        self._filter_key = key
        self._render()

    def _selected_path(self) -> Optional[str]:
        t = self._selected()
        return t.path if t else None

    def _render(self) -> None:
        keep = self._selected_path()
        self._shown = task_view.visible(self._tasks, self._filter_key, self._search.text())
        table = self._table
        table.setSortingEnabled(False)
        table.blockSignals(True)
        table.setRowCount(len(self._shown))
        for r, t in enumerate(self._shown):
            self._fill_row(r, t)
        table.blockSignals(False)
        table.setSortingEnabled(True)
        self._restore_selection(keep)
        self._status_lbl.setText(f"{len(self._shown)} of {len(self._tasks)} task(s)")
        self._sync_buttons()

    def _fill_row(self, r: int, t: TaskInfo) -> None:
        d = task_view.details_of(t)
        folder = t.path.rpartition("\\")[0] or "\\"
        result = task_view.describe_result(t.last_result_code)
        values = [t.name, folder, t.status, t.last_run, result.split(":")[0],
                  "-" if t.next_run == "Never" else t.next_run, d.run_as, ", ".join(d.triggers) or t.triggers, t.author]
        for c, v in enumerate(values):
            item = centered_item(str(v))
            item.setData(Qt.ItemDataRole.UserRole, t.path)
            if c == _COL_RESULT:
                item.setToolTip(result)
                if task_view.failed(t.last_result_code):
                    item.setForeground(self._brush("error"))
            elif c == _COL_STATE and t.status == "Disabled":
                item.setForeground(self._brush("warning"))
            self._table.setItem(r, c, item)

    @staticmethod
    def _brush(meaning: str):
        from PyQt6.QtGui import QColor
        return QColor(semantic(meaning))

    def _restore_selection(self, path: Optional[str]) -> None:
        if not path:
            return
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == path:
                self._table.selectRow(r)
                return

    # ------------------------------------------------------------------
    # Selection and detail
    # ------------------------------------------------------------------
    def _selected(self) -> Optional[TaskInfo]:
        rows = self._table.selectionModel().selectedRows() if self._widget else []
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 0)
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((t for t in self._tasks if t.path == path), None)

    def _on_task_selected(self) -> None:
        t = self._selected()
        self._sync_buttons()
        if t is None:
            self._detail.clear()
            self._xml_view.clear()
            return
        self._detail.setPlainText(task_view.detail_text(t))
        self._xml_view.setPlainText(t.xml)

    def _sync_buttons(self) -> None:
        t = self._selected() if hasattr(self, "_table") else None
        has = t is not None
        self._enable_btn.setEnabled(has and t.status == "Disabled")
        self._disable_btn.setEnabled(has and t.status != "Disabled")
        for b in (self._run_btn, self._delete_btn, self._export_btn, self._copy_btn, self._sig_btn):
            b.setEnabled(has)

    # ------------------------------------------------------------------
    # Actions: confirm, run on a COM worker, report the read-back result
    # ------------------------------------------------------------------
    def _act(self, label: str, fn: Callable, path: str) -> None:
        self._status_lbl.setText(f"{label}...")
        worker = COMWorker(lambda _w: fn(path))
        worker.signals.result.connect(lambda res, lb=label: self._action_done(lb, res))
        worker.signals.error.connect(self._on_error)
        self._start(worker)

    def _action_done(self, label: str, result) -> None:
        if not _alive(self._widget):
            return
        ok, message = result
        self._status_lbl.setText(("Done: " if ok else "FAILED: ") + message)
        self._detail.appendPlainText(f"\n[{label}] {message}")
        self._load_tasks()

    def _change_enabled(self, enabled: bool) -> None:
        t = self._selected()
        if t is None:
            return
        verb = "Enable" if enabled else "Disable"
        if not confirm_destructive(self._widget, f"{verb} Task",
                                   f"{verb} scheduled task '{t.name}'?", irreversible=False):
            return
        self._act(f"{verb} {t.name}", lambda p: task_actions.set_enabled(p, enabled), t.path)

    def _run_selected(self) -> None:
        t = self._selected()
        if t is None:
            return
        if not confirm_destructive(self._widget, "Run Task",
                                   f"Run '{t.name}' now?",
                                   detail=task_view.detail_text(t).split("Runs:")[-1][:300],
                                   irreversible=False):
            return
        self._act(f"Run {t.name}", task_actions.run_now, t.path)

    def _delete_selected(self) -> None:
        t = self._selected()
        if t is None:
            return
        if not confirm_destructive(self._widget, "Delete Task",
                                   f"Delete scheduled task '{t.name}'?",
                                   detail="Export its XML first if you may want it back."):
            return
        self._act(f"Delete {t.name}", task_actions.delete, t.path)

    def _export_selected(self) -> None:
        t = self._selected()
        if t is None:
            return
        path, _ = QFileDialog.getSaveFileName(self._widget, "Export XML", f"{t.name}.xml", "XML (*.xml)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(t.xml)
            self._status_lbl.setText(f"Exported: {os.path.basename(path)}")

    def _copy_selected(self) -> None:
        t = self._selected()
        if t is not None:
            QApplication.clipboard().setText(task_view.detail_text(t))
            self._status_lbl.setText(f"Copied details of {t.name}")

    def _verify_selected(self) -> None:
        t = self._selected()
        if t is None:
            return
        details = task_view.details_of(t)
        if not details.commands:
            self._status_lbl.setText("This task has no program to verify.")
            return
        program = task_view.program_path(details.commands[0][0])
        self._status_lbl.setText("Checking signature...")
        worker = Worker(lambda _w: task_view_signature(program))
        worker.signals.result.connect(self._signature_done)
        worker.signals.error.connect(self._on_error)
        self._start(worker)

    def _signature_done(self, text: str) -> None:
        if _alive(self._widget):
            self._status_lbl.setText(text)
            self._detail.appendPlainText("\n[Signature] " + text)


def task_view_signature(program: str) -> str:
    """One sentence about the program's Authenticode signature (worker thread)."""
    if not program or not os.path.isabs(program):
        return f"Cannot verify '{program}': not an absolute path."
    if not os.path.exists(program):
        return f"{program} does not exist."
    from core.procengine.signatures import verify_signature
    facts = verify_signature(program)
    who = f" by {facts.signer}" if facts.signer else ""
    extra = f" ({facts.reason})" if facts.reason else ""
    return f"{os.path.basename(program)}: {facts.status}{who}{extra}"
