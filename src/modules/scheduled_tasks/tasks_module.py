import os
import subprocess
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTreeWidget, QTreeWidgetItem, QSplitter,
    QPlainTextEdit, QHeaderView, QProgressBar, QLabel, QMessageBox,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QThreadPool

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import COMWorker
from modules.scheduled_tasks.tasks_reader import (
    TaskInfo, TaskFolder, get_folder_tree, get_tasks_in_folder,
)

TASK_COLS = ["Name", "Status", "Last Run", "Last Result", "Next Run", "Author", "Triggers"]


class TasksModule(BaseModule):
    name = "Scheduled Tasks"
    icon = "📅"
    description = "View and manage scheduled tasks"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None

    # ------------------------------------------------------------------
    # BaseModule lifecycle
    # ------------------------------------------------------------------

    def on_start(self, app) -> None:
        self.app = app

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        if hasattr(self, "_tasks_load_fn"):
            self._tasks_load_fn()
        elif hasattr(self, "_load_tree"):
            self._load_tree()

    def on_activate(self) -> None:
        if not getattr(self, "_tasks_loaded", False) and hasattr(self, "_tasks_load_fn"):
            self._tasks_loaded = True
            self._tasks_load_fn()

    def on_deactivate(self) -> None:
        pass

    def on_stop(self) -> None:
        self.cancel_all_workers()

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def create_widget(self) -> QWidget:
        w = self._widget = QWidget()
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        enable_btn = QPushButton("Enable")
        disable_btn = QPushButton("Disable")
        run_btn = QPushButton("Run Now")
        delete_btn = QPushButton("Delete")
        export_btn = QPushButton("Export XML")
        taskschd_btn = QPushButton("Open Task Scheduler")
        status_lbl = QLabel("Select a folder in the tree.")

        for b in (enable_btn, disable_btn, run_btn, delete_btn, export_btn):
            b.setEnabled(False)

        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(enable_btn)
        toolbar.addWidget(disable_btn)
        toolbar.addWidget(run_btn)
        toolbar.addWidget(delete_btn)
        toolbar.addWidget(export_btn)
        toolbar.addWidget(taskschd_btn)
        toolbar.addStretch()
        toolbar.addWidget(status_lbl)
        main_layout.addLayout(toolbar)

        # Indeterminate progress bar
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setFixedHeight(4)
        progress.hide()
        main_layout.addWidget(progress)

        # ── Splitter: folder tree | task table + XML detail ──────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)

        # Left: folder tree
        folder_tree = QTreeWidget()
        folder_tree.setHeaderLabel("Task Folders")
        folder_tree.setMinimumWidth(200)
        splitter.addWidget(folder_tree)

        # Right: task table + XML detail
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        task_table = QTableWidget(0, len(TASK_COLS))
        task_table.setHorizontalHeaderLabels(TASK_COLS)
        task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        center_header(task_table)
        task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        xml_view = QPlainTextEdit()
        xml_view.setReadOnly(True)
        xml_view.setMaximumHeight(200)
        xml_view.setPlaceholderText("Select a task to view its XML definition.")

        right_layout.addWidget(task_table, 1)
        right_layout.addWidget(xml_view)
        splitter.addWidget(right)
        splitter.setSizes([250, 750])

        # Mutable reference to the current task list
        tasks_ref: list = [[]]  # tasks_ref[0] = List[TaskInfo]
        # ── Folder tree loading ───────────────────────────────────────
        def load_folder_tree():
            refresh_btn.setEnabled(False)
            progress.show()
            status_lbl.setText("Loading folders...")
            folder_tree.clear()

            worker = COMWorker(lambda _w: get_folder_tree())

            def on_folders(root_folder: TaskFolder):
                refresh_btn.setEnabled(True)
                progress.hide()
                folder_tree.clear()

                def add_folder(parent_item, tf: TaskFolder):
                    item = QTreeWidgetItem(
                        parent_item if parent_item else folder_tree,
                        [tf.name],
                    )
                    item.setData(0, Qt.ItemDataRole.UserRole, tf.path)
                    for sub in tf.subfolders:
                        add_folder(item, sub)
                    return item

                root_item = add_folder(None, root_folder)
                folder_tree.expandItem(root_item)
                # Show the root's tasks at once; the tab used to open empty.
                folder_tree.setCurrentItem(root_item)
                folder_tree.itemClicked.emit(root_item, 0)

            def on_error(err: str):
                refresh_btn.setEnabled(True)
                progress.hide()
                status_lbl.setText(f"Error: {err}")

            worker.signals.result.connect(on_folders)
            worker.signals.error.connect(on_error)
            self._workers.append(worker)
            if self.app and hasattr(self.app, "thread_pool"):
                self.app.thread_pool.start(worker)
            else:
                QThreadPool.globalInstance().start(worker)

        # ── Task table loading ────────────────────────────────────────
        def load_tasks_for_folder(folder_path: str):
            progress.show()
            status_lbl.setText(f"Loading tasks in {folder_path}...")
            task_table.setRowCount(0)
            xml_view.clear()

            worker = COMWorker(lambda _w: get_tasks_in_folder(folder_path))

            def on_tasks(tasks: List[TaskInfo]):
                tasks_ref[0] = tasks
                progress.hide()
                task_table.setRowCount(len(tasks))
                for r, t in enumerate(tasks):
                    vals = [
                        t.name, t.status, t.last_run, t.last_result,
                        t.next_run, t.author, t.triggers,
                    ]
                    for c, v in enumerate(vals):
                        task_table.setItem(r, c, centered_item(str(v)))
                status_lbl.setText(f"{len(tasks)} task(s) in {folder_path}")

            def on_error(err: str):
                progress.hide()
                status_lbl.setText(f"Error: {err}")

            worker.signals.result.connect(on_tasks)
            worker.signals.error.connect(on_error)
            self._workers.append(worker)
            if self.app and hasattr(self.app, "thread_pool"):
                self.app.thread_pool.start(worker)
            else:
                QThreadPool.globalInstance().start(worker)

        # ── Helpers ───────────────────────────────────────────────────
        def selected_task() -> Optional[TaskInfo]:
            rows = {i.row() for i in task_table.selectedIndexes()}
            if rows:
                r = min(rows)
                if r < len(tasks_ref[0]):
                    return tasks_ref[0][r]
            return None

        def _on_task_selected():
            t = selected_task()
            has = t is not None
            enable_btn.setEnabled(has)
            disable_btn.setEnabled(has)
            run_btn.setEnabled(has)
            delete_btn.setEnabled(has)
            export_btn.setEnabled(has)
            if t:
                xml_view.setPlainText(t.xml)

        def _run_task_action(action_fn):
            t = selected_task()
            if not t:
                return
            try:
                action_fn(t)
                status_lbl.setText(f"Done: {t.name}")
                item = folder_tree.currentItem()
                if item:
                    load_tasks_for_folder(
                        item.data(0, Qt.ItemDataRole.UserRole) or "\\"
                    )
            except Exception as e:
                status_lbl.setText(f"Error: {e}")

        # ── Task actions ──────────────────────────────────────────────
        def _enable_task(t: TaskInfo):
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                svc = win32com.client.Dispatch("Schedule.Service")
                svc.Connect()
                folder_path = "\\".join(t.path.split("\\")[:-1]) or "\\"
                folder = svc.GetFolder(folder_path)
                task_obj = folder.GetTask(t.name)
                task_def = task_obj.Definition
                task_def.Settings.Enabled = True
                folder.RegisterTaskDefinition(t.name, task_def, 6, "", "", 3, "")
            finally:
                pythoncom.CoUninitialize()

        def _disable_task(t: TaskInfo):
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                svc = win32com.client.Dispatch("Schedule.Service")
                svc.Connect()
                folder_path = "\\".join(t.path.split("\\")[:-1]) or "\\"
                folder = svc.GetFolder(folder_path)
                task_obj = folder.GetTask(t.name)
                task_def = task_obj.Definition
                task_def.Settings.Enabled = False
                folder.RegisterTaskDefinition(t.name, task_def, 6, "", "", 3, "")
            finally:
                pythoncom.CoUninitialize()

        def _run_task_now(t: TaskInfo):
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                svc = win32com.client.Dispatch("Schedule.Service")
                svc.Connect()
                folder_path = "\\".join(t.path.split("\\")[:-1]) or "\\"
                folder = svc.GetFolder(folder_path)
                folder.GetTask(t.name).Run("")
            finally:
                pythoncom.CoUninitialize()

        def _delete_task(t: TaskInfo):
            reply = QMessageBox.question(
                w, "Delete Task",
                f"Delete task '{t.name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                import win32com.client
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    svc = win32com.client.Dispatch("Schedule.Service")
                    svc.Connect()
                    folder_path = "\\".join(t.path.split("\\")[:-1]) or "\\"
                    folder = svc.GetFolder(folder_path)
                    folder.DeleteTask(t.name, 0)
                finally:
                    pythoncom.CoUninitialize()

        def _export_xml(t: TaskInfo):
            path, _ = QFileDialog.getSaveFileName(
                w, "Export XML", f"{t.name}.xml", "XML (*.xml)"
            )
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(t.xml)
                status_lbl.setText(f"Exported: {os.path.basename(path)}")

        # ── Wire up signals ───────────────────────────────────────────
        folder_tree.itemClicked.connect(
            lambda item, col: load_tasks_for_folder(
                item.data(0, Qt.ItemDataRole.UserRole) or "\\"
            )
        )
        task_table.selectionModel().selectionChanged.connect(
            lambda: _on_task_selected()
        )
        refresh_btn.clicked.connect(load_folder_tree)
        taskschd_btn.clicked.connect(
            lambda: subprocess.Popen(["taskschd.msc"], shell=True,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        )
        enable_btn.clicked.connect(lambda: _run_task_action(_enable_task))
        disable_btn.clicked.connect(lambda: _run_task_action(_disable_task))
        run_btn.clicked.connect(lambda: _run_task_action(_run_task_now))
        delete_btn.clicked.connect(lambda: _run_task_action(_delete_task))
        export_btn.clicked.connect(lambda: _run_task_action(lambda t: _export_xml(t)))

        self._tasks_load_fn = load_folder_tree
        return w
