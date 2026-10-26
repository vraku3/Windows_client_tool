# src/modules/remote_tools/remote_module.py
import logging
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6 import sip
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCompleter, QFormLayout, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QTableWidget, QTabWidget, QVBoxLayout, QWidget, QGridLayout, QScrollArea,
)

from ui.module_intro import module_intro
from ui.help.network_text import INTRO
from ui.help.network_ui import control_help, explain_tabs, item_help

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import centered_item, set_role
from modules.remote_tools import remote_audit as ra
from core.worker import Worker
from ui.table_style import apply_table_style
from ui.help.network_overview import add_glance, feature_card, unreadable
from ui.empty_state import EmptyState

logger = logging.getLogger(__name__)


def _send_wol(mac: str, broadcast: str = "255.255.255.255") -> None:
    """Send Wake-on-LAN magic packet."""
    mac_clean = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(mac_clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    mac_bytes = bytes.fromhex(mac_clean)
    magic = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(magic, (broadcast, 9))


def _ping_host(host: str) -> bool:
    result = subprocess.run(
        ["ping", "-n", "1", "-w", "500", host],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    return result.returncode == 0


class RemoteToolsModule(BaseModule):
    name = "Remote Tools"
    icon = "🚀"
    description = "RDP launcher, WinRS console, Ping Sweep, and Wake-on-LAN."
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._history: List[str] = []
        self._widget: QWidget | None = None

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(module_intro(INTRO["remote"]))
        layout.setContentsMargins(4, 4, 4, 4)

        tabs = QTabWidget()
        tabs.addTab(self._build_exposure_tab(), "🔒 Exposure")
        tabs.addTab(self._build_rdp_tab(), "🖥️ RDP")
        tabs.addTab(self._build_winrs_tab(), "💻 WinRS")
        tabs.addTab(self._build_ping_sweep_tab(), "📡 Ping Sweep")
        tabs.addTab(self._build_wol_tab(), "⚡ Wake-on-LAN")
        layout.addWidget(tabs)
        explain_tabs(tabs, "remote")

        self._widget = root
        return root

    # ------------------------------------------------------------------
    # Exposure tab (what remote access is switched on here)
    # ------------------------------------------------------------------

    def _build_exposure_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._glance = add_glance(layout, [
            ('on', 'Features on', 'warning', 'Remote-access features Windows reports as on. This is configuration, not an external reachability test.'),
            ('off', 'Features off', 'ok', 'Remote-access features Windows positively reports as off.'),
            ('unknown', 'Could not read', 'warning', 'Features whose state could not be read. These are never counted as off.'),
            ('attention', 'Need attention', 'danger', 'Features carrying high or medium severity exposure findings.'),
        ])
        top = QHBoxLayout()
        self._exp_refresh = QPushButton("Refresh")
        self._exp_refresh.clicked.connect(self._load_exposure)
        self._exp_jump = QPushButton("Open related page")
        self._exp_jump.setEnabled(False)
        self._exp_jump.clicked.connect(self._jump_selected)
        self._exp_status = QLabel("")
        self._exp_details = QPushButton('Show table')
        self._exp_details.setCheckable(True)
        self._exp_details.setEnabled(False)
        self._exp_details.setToolTip('Switch between feature cards and the detailed exposure table. Select a table row to open its related page.')
        self._exp_details.toggled.connect(self._toggle_exposure_details)
        set_role(self._exp_status, "muted")
        top.addWidget(self._exp_refresh)
        top.addWidget(self._exp_jump)
        top.addWidget(self._exp_details)
        top.addWidget(self._exp_status, 1)
        layout.addLayout(top)
        self._exp_empty = EmptyState('shield', 'Remote access', 'Reading local remote-access settings.',
                                     variant='loading')
        layout.addWidget(self._exp_empty)
        self._feature_area = QScrollArea()
        self._feature_area.setWidgetResizable(True)
        self._feature_area.setToolTip('Local remote-access features and their measured exposure findings.')
        self._feature_page = QWidget()
        self._feature_grid = QGridLayout(self._feature_page)
        self._feature_area.setWidget(self._feature_page)
        layout.addWidget(self._feature_area, 1)
        self._feature_area.hide()
        self._feature_cards = []
        self._exp_table = QTableWidget(0, 4)
        self._exp_table.setHorizontalHeaderLabels(["Feature", "Status", "Details", "Findings"])
        self._exp_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._exp_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._exp_table.verticalHeader().setVisible(False)
        hh = self._exp_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._exp_table.itemSelectionChanged.connect(self._exp_selection)
        apply_table_style(self._exp_table)
        self._exp_table.hide()
        layout.addWidget(self._exp_table, 1)
        self._exp_rows: list = []
        self._exp_loaded = False
        return w

    def _toggle_exposure_details(self, checked):
        self._exp_table.setVisible(checked)
        self._feature_area.setVisible(not checked and bool(self._exp_rows))
        self._exp_details.setText('Show cards' if checked else 'Show table')

    def _load_exposure(self) -> None:
        self._exp_refresh.setEnabled(False)
        self._exp_status.setText("Reading remote-access settings...")
        unreadable(self._glance, 'Reading remote-access settings')
        w = Worker(lambda _w: ra.evaluate(ra.gather()))
        w.signals.result.connect(self._on_exposure)
        w.signals.error.connect(self._on_exposure_error)
        w.signals.cancelled.connect(self._exposure_cancelled)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _exposure_cancelled(self) -> None:
        if self._widget is None or sip.isdeleted(self._widget):
            return
        self._exp_refresh.setEnabled(True)
        self._exp_loaded = False
        unreadable(self._glance, 'Read cancelled; refresh to try again')
        self._exp_status.setText('Read cancelled')

    def _on_exposure_error(self, err: str) -> None:
        if self._widget is None or sip.isdeleted(self._widget):
            return
        self._exp_refresh.setEnabled(True)
        self._exp_status.setText("Could not read remote-access state: %s" % err)
        unreadable(self._glance, str(err))
        self._feature_area.hide()
        self._exp_table.hide()
        self._exp_details.setEnabled(False)
        self._exp_empty.set_variant('error', reason=str(err))
        self._exp_empty.show()

    def _on_exposure(self, rows) -> None:
        if self._widget is None or sip.isdeleted(self._widget):
            return
        self._exp_refresh.setEnabled(True)
        self._exp_rows = rows
        self._show_feature_cards(rows)
        self._exp_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = [r.feature, r.status, r.summary, " ".join(r.findings)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if r.severity in ("high", "medium"):
                    item.setForeground(QColor(semantic("error" if r.severity == "high" else "warning")))
                self._exp_table.setItem(i, c, item_help(item, self._exp_table, c))
        n = sum(1 for r in rows if r.status == "On")
        self._exp_status.setText("%d of %d remote-access features are on." % (n, len(rows)))

    def _show_feature_cards(self, rows) -> None:
        values = {'on': sum(r.status == 'On' for r in rows),
                  'off': sum(r.status == 'Off' for r in rows),
                  'unknown': sum(r.status == 'Unknown' for r in rows),
                  'attention': sum(r.severity in ('high', 'medium') for r in rows)}
        for key, count in values.items():
            self._glance[key].set(count, 'Features in this read')
        while self._feature_grid.count():
            item = self._feature_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._feature_cards = []
        for index, row in enumerate(rows):
            role = 'statusError' if row.severity == 'high' else 'statusWarning' if row.severity == 'medium' else 'statusInfo'
            detail = row.summary + ('\n\n' + '\n'.join(row.findings) if row.findings else '')
            card = feature_card(row.feature, detail, status=row.status, role=role)
            if row.jump:
                button = QPushButton('Open ' + row.jump)
                button.setToolTip('Inspect related settings in ' + row.jump + '. This button does not change Windows.')
                button.clicked.connect(lambda _=False, i=index: self._open_feature(i))
                card.layout().addWidget(button)
            self._feature_grid.addWidget(card, index // 2, index % 2)
            self._feature_cards.append(card)
        self._feature_area.setVisible(bool(rows))
        self._exp_details.setEnabled(bool(rows))
        self._toggle_exposure_details(self._exp_details.isChecked())
        self._exp_empty.setVisible(not rows)
        self._exp_empty.set_variant('empty')

    def _open_feature(self, index) -> None:
        self._exp_table.selectRow(index)
        self._jump_selected()

    def _exp_selection(self) -> None:
        rows = self._exp_table.selectionModel().selectedRows() if self._exp_table.selectionModel() else []
        target = self._exp_rows[rows[0].row()].jump if rows and rows[0].row() < len(self._exp_rows) else ""
        self._exp_jump.setEnabled(bool(target))
        self._exp_jump.setText("Open %s" % target if target else "Open related page")

    def _jump_selected(self) -> None:
        rows = self._exp_table.selectionModel().selectedRows()
        if not rows or self.app is None:
            return
        target = self._exp_rows[rows[0].row()].jump
        if target:
            from core.events import NAV_REQUEST_MODULE, NavRequestData
            self.app.event_bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name=target))

    # ------------------------------------------------------------------
    # RDP tab
    # ------------------------------------------------------------------

    def _build_rdp_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        form = QFormLayout()
        self._rdp_host = self._host_input("rdp")
        form.addRow("Hostname / IP:", self._rdp_host)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        connect_btn = QPushButton("🔗 Connect (mstsc)")
        connect_btn.clicked.connect(self._rdp_connect)
        btn_row.addWidget(connect_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    def _rdp_connect(self) -> None:
        host = self._rdp_host.text().strip()
        if not host:
            return
        self._add_history(host)
        subprocess.Popen(["mstsc", f"/v:{host}"])

    # ------------------------------------------------------------------
    # WinRS tab
    # ------------------------------------------------------------------

    def _build_winrs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        self._winrs_host = self._host_input("winrs")
        top.addWidget(QLabel("Host:"))
        top.addWidget(self._winrs_host, stretch=1)
        self._winrs_cmd = QLineEdit()
        control_help(self._winrs_cmd, 'self._winrs_cmd')
        self._winrs_cmd.setPlaceholderText("Command to run remotely")
        self._winrs_cmd.returnPressed.connect(self._winrs_run)
        top.addWidget(self._winrs_cmd, stretch=2)
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self._winrs_run)
        top.addWidget(run_btn)
        layout.addLayout(top)

        self._winrs_output = QPlainTextEdit()
        self._winrs_output.setReadOnly(True)
        self._winrs_output.setPlaceholderText("Output appears here…")
        layout.addWidget(self._winrs_output)
        return w

    def _winrs_run(self) -> None:
        host = self._winrs_host.text().strip()
        cmd = self._winrs_cmd.text().strip()
        if not host or not cmd:
            return
        self._add_history(host)
        self._winrs_output.clear()
        self._winrs_output.appendPlainText(f"$ winrs -r:{host} {cmd}\n")

        def work(worker):
            result = subprocess.run(
                ["winrs", f"-r:{host}", cmd],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout + result.stderr

        def on_result(text):
            self._winrs_output.appendPlainText(text)

        w = Worker(work)
        w.signals.result.connect(on_result)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    # ------------------------------------------------------------------
    # Ping Sweep tab
    # ------------------------------------------------------------------

    def _build_ping_sweep_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("Subnet prefix:"))
        self._sweep_prefix = QLineEdit("192.168.1")
        control_help(self._sweep_prefix, 'self._sweep_prefix')
        self._sweep_prefix.setMaximumWidth(140)
        top.addWidget(self._sweep_prefix)
        top.addWidget(QLabel(".1 –"))
        self._sweep_end = QSpinBox()
        control_help(self._sweep_end, 'self._sweep_end')
        self._sweep_end.setRange(2, 254)
        self._sweep_end.setValue(254)
        top.addWidget(self._sweep_end)
        self._sweep_btn = QPushButton("Sweep")
        self._sweep_btn.clicked.connect(self._run_sweep)
        top.addWidget(self._sweep_btn)
        self._sweep_progress = QProgressBar()
        self._sweep_progress.setVisible(False)
        top.addWidget(self._sweep_progress)
        top.addStretch()
        layout.addLayout(top)

        self._sweep_table = QTableWidget(0, 2)
        self._sweep_table.setHorizontalHeaderLabels(["IP Address", "Status"])
        apply_table_style(self._sweep_table)
        self._sweep_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._sweep_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._sweep_table.verticalHeader().setVisible(False)
        layout.addWidget(self._sweep_table)
        return w

    def _run_sweep(self) -> None:
        prefix = self._sweep_prefix.text().strip()
        end = self._sweep_end.value()
        hosts = [f"{prefix}.{i}" for i in range(1, end + 1)]

        self._sweep_table.setRowCount(0)
        self._sweep_btn.setEnabled(False)
        self._sweep_progress.setVisible(True)
        self._sweep_progress.setRange(0, len(hosts))
        self._sweep_progress.setValue(0)

        done = [0]

        def work(worker):
            results = []
            with ThreadPoolExecutor(max_workers=50) as pool:
                futures = {pool.submit(_ping_host, h): h for h in hosts}
                for fut in as_completed(futures):
                    if worker.is_cancelled:
                        break
                    h = futures[fut]
                    alive = fut.result()
                    results.append((h, alive))
                    done[0] += 1
                    worker.signals.progress.emit(done[0])
            results.sort(key=lambda t: tuple(int(x) for x in t[0].split(".")))
            return results

        def on_result(results):
            self._sweep_table.setRowCount(0)
            for ip, alive in results:
                row = self._sweep_table.rowCount()
                self._sweep_table.insertRow(row)
                self._sweep_table.setItem(row, 0, item_help(centered_item(ip), self._sweep_table, 0))
                status = "🟢 Online" if alive else "⚫ Offline"
                self._sweep_table.setItem(row, 1, item_help(centered_item(status), self._sweep_table, 1))

        def on_done():
            self._sweep_btn.setEnabled(True)
            self._sweep_progress.setVisible(False)

        w = Worker(work)
        w.signals.result.connect(on_result)
        w.signals.finished.connect(on_done)
        w.signals.progress.connect(self._sweep_progress.setValue)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    # ------------------------------------------------------------------
    # Wake-on-LAN tab
    # ------------------------------------------------------------------

    def _build_wol_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        form = QFormLayout()
        self._wol_mac = QLineEdit()
        control_help(self._wol_mac, 'self._wol_mac')
        self._wol_mac.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        form.addRow("MAC Address:", self._wol_mac)
        self._wol_broadcast = QLineEdit("255.255.255.255")
        control_help(self._wol_broadcast, 'self._wol_broadcast')
        # The input help explains that directed broadcasts also need routing support.
        form.addRow("Broadcast IP:", self._wol_broadcast)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        send_btn = QPushButton("⚡ Send Magic Packet")
        send_btn.clicked.connect(self._send_wol)
        btn_row.addWidget(send_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._wol_status = QLabel("")
        layout.addWidget(self._wol_status)
        layout.addStretch()
        return w

    def _send_wol(self) -> None:
        mac = self._wol_mac.text().strip()
        broadcast = self._wol_broadcast.text().strip() or "255.255.255.255"
        try:
            _send_wol(mac, broadcast)
            self._wol_status.setText(f"✅ Magic packet sent to {mac} via {broadcast}")
        except Exception as e:
            self._wol_status.setText(f"❌ Failed: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _host_input(self, tag: str) -> QLineEdit:
        le = QLineEdit()
        control_help(le, 'le')
        le.setPlaceholderText("hostname or IP")
        completer = QCompleter(self._history, le)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        le.setCompleter(completer)
        return le

    def _add_history(self, host: str) -> None:
        if host and host not in self._history:
            self._history.insert(0, host)
            self._history = self._history[:20]
            if self.app is not None and getattr(self.app, "config", None) is not None:
                self.app.config.set("remote_tools.history", self._history)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_activate(self) -> None:
        if self._widget is not None and not getattr(self, "_exp_loaded", True):
            self._exp_loaded = True
            self._load_exposure()
        if self.app is not None and getattr(self.app, "config", None) is not None:
            self._history = self.app.config.get("remote_tools.history", [])

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_start(self, app) -> None:
        self.app = app
        if getattr(app, "config", None) is not None:
            self._history = app.config.get("remote_tools.history", [])

    def on_stop(self) -> None:
        self.cancel_all_workers()
