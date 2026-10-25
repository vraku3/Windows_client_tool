import winreg
from PyQt6 import sip
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTabWidget, QLabel,
    QProgressBar, QStackedWidget, QScrollArea)
from ui.help.network_ui import explain_tabs, item_help
from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker, retire
from core.table_ui import centered_item, fit_last
from ui.table_style import apply_table_style
from ui.empty_state import EmptyState
from ui.help.network_overview import add_glance, feature_card, unreadable
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
    rows = [{"Severity": 'UNKNOWN' if f.key.startswith('unreadable') else f.severity.upper(),
             "Finding": f.title, "Detail": f.detail}
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
    apply_table_style(t, numeric_columns=[i for i, c in enumerate(columns)
                                         if c in ('Users', 'Opens', 'Connected (sec)', 'Idle (sec)')])
    return t


def _fill_table(table: QTableWidget, rows: List[Dict], columns: List[str]):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, col in enumerate(columns):
            table.setItem(r, c, item_help(centered_item(str(row.get(col, ""))), table, c))

class _RefreshTab(QWidget):
    def __init__(self, loader_fn, columns, thread_pool, parent=None):
        super().__init__(parent)
        self._loader = loader_fn
        self._columns = columns
        self._thread_pool = thread_pool
        self._worker: Optional[Worker] = None
        self._scanning = False
        self._loaded = False
        self._workers = []
        layout = QVBoxLayout(self)
        self._glance = add_glance(layout, self._summary_specs())
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
        self._content = QStackedWidget()
        self._content.addWidget(self._table)
        self._empty = EmptyState('📂', 'No resources to show',
                                 'Refresh reads this section from Windows without changing it.', 'Refresh')
        self._empty.action_triggered.connect(self.refresh)
        self._content.addWidget(self._empty)
        self._cards = QScrollArea()
        self._cards.setWidgetResizable(True)
        self._cards.setToolTip('Resource cards. Every field comes from the current Windows read.')
        self._content.addWidget(self._cards)
        self._content.setCurrentWidget(self._empty)
        layout.addWidget(self._content, 1)
        self._refresh_btn.clicked.connect(self._load)

    def _summary_specs(self):
        if 'Severity' in self._columns:
            extras = [('attention', 'Need attention'), ('unknown', 'Could not read')]
        elif 'Encrypted' in self._columns:
            extras = [('encrypted', 'Encrypted shares'), ('users', 'Share connections')]
        elif 'Client' in self._columns:
            extras = [('clients', 'Clients'), ('users', 'Users')]
        else:
            extras = [('persistent', 'Persistent mappings'), ('network', 'Network mappings')]
        caption = ('Findings and settings' if 'Severity' in self._columns else 'Shares' if 'Name' in self._columns
                   else 'Sessions' if 'Opens' in self._columns else 'Open files' if 'Client' in self._columns else 'Mapped drives')
        return [('items', caption, 'info', 'Number of rows returned by the current section read.')] + [
            (key, label, 'warning' if key in ('attention', 'unknown') else 'info',
             label + ' reported by this read. An unavailable section is unknown, never zero.') for key, label in extras]

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
        unreadable(self._glance, 'Reading this section from Windows')
        self._empty.set_variant('loading')
        self._empty.action_button.setEnabled(False)
        self._content.setCurrentWidget(self._empty)
        self._progress.show()
        loader = self._loader
        worker = Worker(lambda _w: loader())
        self._worker = worker
        def on_result(rows):
            if not sip.isdeleted(self) and self._worker is worker:
                self._show_rows(rows)
        def on_error(err):
            if not sip.isdeleted(self) and self._worker is worker:
                self._show_error(str(err))
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        worker.signals.cancelled.connect(lambda: self.cancel_all() if not sip.isdeleted(self) and self._worker is worker else None)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _finish_read(self):
        self._scanning = False
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._empty.action_button.setEnabled(True)

    def _show_rows(self, rows):
        self._finish_read()
        self._loaded = True
        _fill_table(self._table, rows, self._columns)
        self._status.setText(f'{len(rows)} item(s).')
        values = {'items': len(rows), 'clients': len({r.get('Client') for r in rows if r.get('Client')}),
                  'users': len({r.get('User') for r in rows if r.get('User')}),
                  'attention': sum(r.get('Severity', '').lower() in ('high', 'medium', 'error', 'warning') for r in rows),
                  'unknown': sum(r.get('Severity', '').lower() == 'unknown' for r in rows),
                  'encrypted': sum(str(r.get('Encrypted', '')).lower() in ('yes', 'true', 'on') for r in rows),
                  'persistent': sum(r.get('Type') == 'Persistent' for r in rows),
                  'network': sum(r.get('Type') == 'Network' for r in rows)}
        if 'Users' in self._columns:
            counts = [str(r.get('Users', '')) for r in rows]
            values['users'] = sum(int(v) for v in counts) if all(v.isdigit() for v in counts) else None
        for key, tile in self._glance.items():
            tile.set(values[key], 'Current section', reason='Windows did not report a numeric user count')
        self._empty.set_variant('empty')
        self._content.setCurrentWidget(self._table if rows else self._empty)
        if 0 < len(rows) <= 6:
            self._show_cards(rows)

    def _show_cards(self, rows):
        page = QWidget()
        layout = QVBoxLayout(page)
        for row in rows:
            exposure = 'Severity' in self._columns
            title = str(row.get('Finding' if exposure else self._columns[0], 'Resource'))
            detail = (str(row.get('Detail', '')) if exposure else
                      '\n'.join(f'{column}: {row.get(column, "")}' for column in self._columns[1:]))
            severity = str(row.get('Severity', '')).lower()
            role = 'statusError' if severity in ('high', 'error') else 'statusWarning' if severity in ('unknown', 'medium', 'warning') else 'statusInfo'
            layout.addWidget(feature_card(title, detail, status=str(row.get('Severity', '')), role=role))
        layout.addStretch()
        old = self._cards.takeWidget()
        if old is not None:
            old.deleteLater()
        self._cards.setWidget(page)
        self._content.setCurrentWidget(self._cards)

    def _show_error(self, err):
        self._finish_read()
        self._status.setText(f'Could not read: {err}')
        unreadable(self._glance, str(err))
        self._empty.set_variant('error', reason=str(err))
        self._content.setCurrentWidget(self._empty)
        logger.warning('Shared resources read failed: %s', err)

    def cancel_all(self) -> None:
        was_scanning = self._scanning
        self._worker = None
        retire(self._workers)
        self._finish_read()
        if was_scanning or self._empty.variant == 'loading':
            self._loaded = False
            self._empty.set_variant('empty', reason='Read cancelled. Refresh to read this section again.')
            unreadable(self._glance, 'Read cancelled')


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
        tabs.currentChanged.connect(lambda _: self.on_activate())
        return explain_tabs(tabs, "shares")
    def on_start(self, app=None):
        self.app = app
    def on_stop(self) -> None:
        self.on_deactivate()
    def on_activate(self):
        if hasattr(self, "_shares_tabs"):
            tab = self._shares_tabs.currentWidget()
            if hasattr(tab, "_load") and not tab._loaded:
                tab._load()
    def on_deactivate(self) -> None:
        self.cancel_all_workers()
        if hasattr(self, "_shares_tabs"):
            for index in range(self._shares_tabs.count()):
                self._shares_tabs.widget(index).cancel_all()

    def refresh_data(self) -> None:
        if hasattr(self, "_shares_tabs"):
            tab = self._shares_tabs.currentWidget()
            if hasattr(tab, "refresh"):
                tab.refresh()

    def get_refresh_interval(self) -> Optional[int]:
        """Auto-refresh every 30 seconds."""
        return 30_000
