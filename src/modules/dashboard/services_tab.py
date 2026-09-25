"""Task Manager's Services tab, as a flat read-mostly table.

Every Windows service as a row with its status, start type, pid and path,
sorted by display name. Right-clicking a row offers Start/Stop/Restart (each
confirmed first) and "Go to process" for the running ones, which is left as
a signal nobody is wired to yet.

The pane is deliberately NOT gated behind elevation: the read is WMI, which
everyone can do, and the Dashboard's other tabs all run unelevated. Actions
are a different story -- starting or stopping a service is refused by the OS
when the app is not running as Administrator, and this pane reports that
refusal rather than pretending the action worked. The header says so.
"""
import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QMenu,
                             QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.confirm import confirm_destructive
from core.semantic_colors import semantic
from core.worker import COMWorker, Worker

from modules.services_manager import services_module

logger = logging.getLogger(__name__)

REFRESH_MS = 5000

#: Task Manager column order. Path is stretched because it is prose; the
#: status and the short codes hug their content.
_HEADERS = ("Status", "Name", "Display Name", "Start Type", "PID",
            "Impact", "Path")
STATUS, NAME, DISPLAY, START_TYPE, PID, IMPACT, PATH = range(len(_HEADERS))

#: WMI state word -> semantic colour name. Anything not listed (the pending
#: states, Unknown) keeps the theme's plain foreground rather than being
#: painted a meaning nobody decided on.
_STATUS_SEMANTIC = {
    "Running": "success",
    "Stopped": "error",
    "Paused": "warning",
}

_NOTE = (
    "Right-click a service to start, stop or restart it. Those actions need "
    "Administrator rights: unelevated, the OS refuses them and the pane "
    "reports the refusal here rather than claiming a change that did not "
    "happen.")

_ACTION_TITLES = {
    "start": "Start service",
    "stop": "Stop service",
    "restart": "Restart service",
}

_ACTION_PAST = {
    "start": "started",
    "stop": "stopped",
    "restart": "restarted",
}


def _marker(value) -> str:
    """An honest dash for a value the read simply had no answer for."""
    text = str(value or "").strip()
    return text if text else "—"


class ServicesTab(QWidget):
    """Every service in one table. Owns its workers, per CLAUDE.md."""

    #: Emitted with a service's pid when the user picks "Go to process".
    #: Nothing is wired to this yet; Process Explorer integration may use it.
    goto_process = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._app = None
        self._busy = False
        self._services: List[Dict] = []
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.note = QLabel(_NOTE, self)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.note)

        top = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        top.addStretch(1)
        layout.addLayout(top)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(False)
        # Items wrap by default, and a long path has no spaces to wrap at, so
        # Qt elided "C:\Program Files\..." to "C:..." -- hiding the one part
        # (the exe name at the end) a reader wants. Middle-elide keeps it.
        self._table.setWordWrap(False)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_menu)
        header = self._table.horizontalHeader()
        # The path column is prose and takes the free width; the short codes
        # hug their content.
        header.setSectionResizeMode(PATH, QHeaderView.ResizeMode.Stretch)
        for column in (STATUS, NAME, DISPLAY, START_TYPE, PID, IMPACT):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

    def set_app(self, app) -> None:
        self._app = app

    def start(self) -> None:
        self.refresh()
        self._timer.start(REFRESH_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()

    def cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    # ---- reading --------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            try:
                self._apply(self._read())
            except Exception as exc:
                # get_services is a WMI read: off the main thread it rides a
                # COMWorker, but the no-pool fallback has nobody to do COM
                # init, so the failure must be named rather than swallowed.
                self._busy = False
                logger.warning("Services refresh failed: %s", exc)
                self.status.setText(f"Could not read the service list: {exc}")
            return
        self._busy = True
        self.status.setText("Reading services…")
        worker = COMWorker(lambda _worker: self._read())
        worker.signals.result.connect(self._apply)
        worker.signals.error.connect(self._failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        self._workers.append(worker)
        pool.start(worker)

    def _read(self) -> List[Dict]:
        # WMI's Win32_Service enumeration. Must run on a COM-initialised
        # thread, which the COMWorker above provides.
        return services_module.get_services()

    def _apply(self, result) -> None:
        self._busy = False
        self._services = list(result)
        self._populate(self._services)
        self.status.setText(f"{len(self._services):,} services")

    def _failed(self, message) -> None:
        self._busy = False
        logger.error("Services refresh failed: %s", message)
        self.status.setText(f"Could not read the service list: {message}")

    def _on_cancelled(self) -> None:
        self._busy = False

    def _populate(self, services: List[Dict]) -> None:
        colors = {state: QColor(semantic(meaning))
                  for state, meaning in _STATUS_SEMANTIC.items()}
        self._table.setRowCount(len(services))
        for row, svc in enumerate(services):
            status = svc.get("Status") or "Unknown"
            pid = _marker(svc.get("PID"))
            values = (status,
                      svc.get("Name") or "—",
                      svc.get("Display Name") or "—",
                      svc.get("Start Type") or "—",
                      pid,
                      svc.get("Impact") or "—",
                      svc.get("Path") or "—")
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    int(Qt.AlignmentFlag.AlignCenter
                        | Qt.AlignmentFlag.AlignVCenter))
                self._table.setItem(row, column, item)
            name_item = self._table.item(row, NAME)
            name_item.setData(Qt.ItemDataRole.UserRole, svc)
            name_item.setToolTip(svc.get("Path") or svc.get("Name") or "")
            color = colors.get(status)
            if color is not None:
                self._table.item(row, STATUS).setForeground(color)

    def _service_at(self, row: int) -> Optional[Dict]:
        if not 0 <= row < self._table.rowCount():
            return None
        item = self._table.item(row, NAME)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # ---- the right-click menu -------------------------------------------

    def _show_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        svc = self._service_at(row)
        if svc is None:
            return
        self._table.selectRow(row)
        menu = self._menu_for(svc)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _menu_for(self, svc: Dict) -> QMenu:
        """Build the row menu without showing it, so tests can inspect it."""
        menu = QMenu(self)
        start = menu.addAction("Start service")
        start.triggered.connect(
            lambda _=False: self._confirmed_action("start", svc))
        stop = menu.addAction("Stop service")
        stop.triggered.connect(
            lambda _=False: self._confirmed_action("stop", svc))
        restart = menu.addAction("Restart service")
        restart.triggered.connect(
            lambda _=False: self._confirmed_action("restart", svc))
        menu.addSeparator()
        pid = _pid_of(svc)
        if pid:
            go = menu.addAction(f"Go to process (PID {pid})")
        else:
            go = menu.addAction("Go to process")
            go.setEnabled(False)
        go.triggered.connect(
            lambda _=False: self._go_to_process(svc, pid))
        return menu

    # ---- actions --------------------------------------------------------

    def _confirmed_action(self, action: str, svc: Dict) -> None:
        """Ask first; nothing destructive happens without a Yes."""
        name = svc.get("Name") or ""
        display = svc.get("Display Name") or name
        message = {
            "start": f"Start the service '{display}'?",
            "stop": f"Stop the service '{display}'?",
            "restart": f"Restart the service '{display}'?",
        }[action]
        if not confirm_destructive(self, _ACTION_TITLES[action], message,
                                   irreversible=False):
            return
        self._run_service_action(action, name, display)

    def _run_service_action(self, action: str, name: str,
                            display: str) -> None:
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            from PyQt6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
        # win32serviceutil is the same machinery the sidebar Services module
        # runs on a plain Worker; only the WMI read needs a COMWorker.
        worker = Worker(
            lambda _worker, a=action, n=name: services_module.service_action(
                n, a))
        worker.signals.result.connect(
            lambda result, a=action, d=display:
                self._on_action_done(a, d, result))
        worker.signals.error.connect(
            lambda message, a=action, d=display:
                self._on_action_failed(a, d, message))
        self._workers.append(worker)
        pool.start(worker)

    def _on_action_done(self, action: str, display: str, result) -> None:
        ok, _running_dependents = result
        if ok:
            self._message(
                _ACTION_TITLES[action],
                f"'{display}' was {_ACTION_PAST[action]}.",
                QMessageBox.Icon.Information)
        else:
            logger.warning("Service %s returned failure: %r", action, result)
            self._message(
                _ACTION_TITLES[action],
                f"Could not {action} '{display}' — the request was refused "
                f"by the service control manager.",
                QMessageBox.Icon.Warning)
        self.refresh()

    def _on_action_failed(self, action: str, display: str, message) -> None:
        logger.warning("Service %s '%s' failed: %s", action, display, message)
        self._message(
            _ACTION_TITLES[action],
            f"Could not {action} '{display}'.\n\n{message}",
            QMessageBox.Icon.Warning)
        self.refresh()

    def _message(self, title: str, text: str, icon) -> None:
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.exec()

    # ---- go to process --------------------------------------------------

    def _go_to_process(self, svc: Dict, pid: int) -> None:
        if not pid:
            return
        logger.info("Go to process for service '%s' -> PID %d (not wired)",
                    svc.get("Name") or "", pid)
        self.goto_process.emit(pid)


def _pid_of(svc: Dict) -> int:
    raw = str(svc.get("PID") or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0
