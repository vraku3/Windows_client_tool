import winreg
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTabWidget, QLabel,
    QProgressBar)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.table_ui import centered_item, fit_last
import logging
logger = logging.getLogger(__name__)


def _smb_data(need: str):
    """The shared SMB read, or an exception carrying WHY a section is missing.

    Raising is what makes the tab say "could not read: ..." instead of
    showing an empty table that reads as "no shares".
    """
    from modules.shared_resources import share_audit as sa
    data = sa.collect_cached(max_age=3.0)
    if data.fatal:
        raise RuntimeError(data.fatal)
    if getattr(data, need) is None:
        raise RuntimeError("Could not read %s: %s" % (need.replace("_", " "),
                                                      data.errors.get(need, "no answer")))
    return data


def get_shares() -> List[Dict]:
    from modules.shared_resources import share_audit as sa
    return sa.rows_shares(_smb_data("shares"))


def get_sessions() -> List[Dict]:
    data = _smb_data("sessions")
    return [{"Client": s.get("Client", ""), "User": s.get("User", ""),
             "Opens": str(s.get("Opens", 0)), "Connected (sec)": str(s.get("Seconds", 0)),
             "Idle (sec)": str(s.get("Idle", 0)), "Dialect": s.get("Dialect", "")}
            for s in data.sessions]


def get_open_files() -> List[Dict]:
    data = _smb_data("open_files")
    return [{"Client": f.get("Client", ""), "User": f.get("User", ""), "Path": f.get("Path", "")}
            for f in data.open_files]


def get_exposure() -> List[Dict]:
    """Findings first (most severe first); the settings behind them last."""
    from modules.shared_resources import share_audit as sa
    data = sa.collect_cached(max_age=3.0)
    rows = [{"Severity": f.severity.upper(), "Finding": f.title, "Detail": f.detail}
            for f in sa.audit(data)]
    if not data.fatal and not rows:
        rows.append({"Severity": "OK", "Finding": "No SMB exposure findings", "Detail": ""})
    srv, cli = data.server, data.client
    if srv:
        rows.append({"Severity": "SETTING", "Finding": "SMB server",
                     "Detail": "SMB1 %s, SMB2 %s, signing required %s, encryption required %s, "
                               "unencrypted access rejected %s" % tuple(
                         "on" if srv.get(k) else "off" for k in
                         ("Smb1", "Smb2", "RequireSigning", "Encrypt", "RejectUnencrypted"))})
    if cli:
        rows.append({"Severity": "SETTING", "Finding": "SMB client",
                     "Detail": "signing required %s, insecure guest logons %s" % (
                         "yes" if cli.get("RequireSigning") else "no",
                         "allowed" if cli.get("InsecureGuest") else "blocked")})
    return rows


def get_mapped_drives() -> List[Dict]:
    drives = []
    # Try winreg HKCU\Network
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network") as k:
            i = 0
            while True:
                try:
                    drive = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, drive) as dk:
                        try:
                            remote, _ = winreg.QueryValueEx(dk, "RemotePath")
                        except OSError:
                            remote = ""
                        try:
                            conn_type, _ = winreg.QueryValueEx(dk, "ConnectionType")
                            type_str = "Persistent" if conn_type == 1 else "Session"
                        except OSError:
                            type_str = ""
                    drives.append({
                        "Drive": f"{drive}:",
                        "Remote Path": remote,
                        "Status": "Connected",
                        "Type": type_str,
                    })
                    i += 1
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("get_mapped_drives: registry enumeration finished", exc_info=True)
                    break
    except OSError:
        logger.debug("Ignored OSError", exc_info=True)
    # Also try win32wnet if available
    try:
        import win32wnet
        import win32netcon
        resource = win32wnet.WNetOpenEnum(win32netcon.RESOURCE_CONNECTED,
                                          win32netcon.RESOURCETYPE_DISK, 0, None)
        while True:
            result = win32wnet.WNetEnumResource(resource, 64)
            if not result:
                break
            for r in result:
                name = r.lpLocalName or ""
                remote = r.lpRemoteName or ""
                if name and not any(d["Drive"] == name for d in drives):
                    drives.append({
                        "Drive": name,
                        "Remote Path": remote,
                        "Status": "Connected",
                        "Type": "Network",
                    })
        win32wnet.WNetCloseEnum(resource)
    except Exception:
        logger.warning("Ignored Exception", exc_info=True)
    return drives


def _make_table(columns) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    fit_last(t)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return t


def _fill_table(table: QTableWidget, rows: List[Dict], columns: List[str]):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, col in enumerate(columns):
            table.setItem(r, c, centered_item(str(row.get(col, ""))))


class _RefreshTab(QWidget):
    def __init__(self, loader_fn, columns, thread_pool, parent=None):
        super().__init__(parent)
        self._loader = loader_fn
        self._columns = columns
        self._thread_pool = thread_pool
        self._worker: Optional[Worker] = None
        self._scanning = False
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._status = QLabel("Click Refresh to load.")
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        toolbar.addWidget(self._refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status)
        layout.addLayout(toolbar)
        layout.addWidget(self._progress)
        self._table = _make_table(columns)
        layout.addWidget(self._table, 1)
        self._refresh_btn.clicked.connect(self._load)

    def refresh(self) -> None:
        """Trigger a background refresh. Idempotent."""
        if self._scanning:
            return
        self._load()

    def _load(self):
        if self._scanning:
            return
        self._scanning = True
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading...")
        self._progress.show()
        loader = self._loader
        self._worker = Worker(lambda _w: loader())
        def on_result(rows):
            self._scanning = False
            self._refresh_btn.setEnabled(True)
            self._progress.hide()
            _fill_table(self._table, rows, self._columns)
            self._status.setText(f"{len(rows)} item(s).")
        def on_error(err):
            self._scanning = False
            self._refresh_btn.setEnabled(True)
            self._progress.hide()
            self._status.setText(f"Error: {err}")
        self._worker.signals.result.connect(on_result)
        self._worker.signals.error.connect(on_error)
        self._thread_pool.start(self._worker)

    def cancel_all(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None


class SharesModule(BaseModule):
    name = "Shared Resources"
    icon = "📂"
    description = "Network shares, sessions, and mapped drives"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def create_widget(self) -> QWidget:
        tabs = QTabWidget()
        tp = self.thread_pool
        tabs.addTab(
            _RefreshTab(get_exposure, ["Severity", "Finding", "Detail"], tp), "Exposure")
        tabs.addTab(
            _RefreshTab(get_shares, ["Name", "Kind", "Path", "Access", "Encrypted", "Users", "Comment"], tp),
            "Network Shares"
        )
        tabs.addTab(
            _RefreshTab(get_sessions, ["Client", "User", "Opens", "Connected (sec)", "Idle (sec)", "Dialect"], tp),
            "Connected Sessions"
        )
        tabs.addTab(_RefreshTab(get_open_files, ["Client", "User", "Path"], tp), "Open Files")
        tabs.addTab(
            _RefreshTab(get_mapped_drives, ["Drive", "Remote Path", "Status", "Type"], tp),
            "Mapped Drives"
        )
        self._shares_tabs = tabs
        return tabs

    def on_start(self, app=None):
        self.app = app
    def on_stop(self) -> None:
        self.cancel_all_workers()
    def on_activate(self):
        if hasattr(self, "_shares_tabs"):
            tab = self._shares_tabs.currentWidget()
            if hasattr(tab, "_load") and hasattr(tab, "_status") and tab._status.text() == "Click Refresh to load.":
                tab._load()
    def on_deactivate(self) -> None:
        self.cancel_all_workers()
        if hasattr(self, "_shares_tabs"):
            tab = self._shares_tabs.currentWidget()
            if hasattr(tab, "cancel_all"):
                tab.cancel_all()

    def refresh_data(self) -> None:
        if hasattr(self, "_shares_tabs"):
            tab = self._shares_tabs.currentWidget()
            if hasattr(tab, "refresh"):
                tab.refresh()

    def get_refresh_interval(self) -> Optional[int]:
        """Auto-refresh every 30 seconds."""
        return 30_000
