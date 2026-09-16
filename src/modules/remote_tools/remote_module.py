# src/modules/remote_tools/remote_module.py
import logging
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QCompleter, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import Worker

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
        layout.setContentsMargins(4, 4, 4, 4)

        tabs = QTabWidget()
        tabs.addTab(self._build_rdp_tab(), "🖥️ RDP")
        tabs.addTab(self._build_winrs_tab(), "💻 WinRS")
        tabs.addTab(self._build_ping_sweep_tab(), "📡 Ping Sweep")
        tabs.addTab(self._build_wol_tab(), "⚡ Wake-on-LAN")
        layout.addWidget(tabs)

        self._widget = root
        return root

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
        self._sweep_prefix.setMaximumWidth(140)
        top.addWidget(self._sweep_prefix)
        top.addWidget(QLabel(".1 –"))
        self._sweep_end = QSpinBox()
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
        center_header(self._sweep_table)
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
                self._sweep_table.setItem(row, 0, centered_item(ip))
                status = "🟢 Online" if alive else "⚫ Offline"
                self._sweep_table.setItem(row, 1, centered_item(status))

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
        self._wol_mac.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        form.addRow("MAC Address:", self._wol_mac)
        self._wol_broadcast = QLineEdit("255.255.255.255")
        self._wol_broadcast.setToolTip("Use directed broadcast (e.g. 192.168.1.255) for cross-VLAN WOL")
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
        le.setPlaceholderText("hostname or IP")
        completer = QCompleter(self._history, le)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        le.setCompleter(completer)
        return le

    def _add_history(self, host: str) -> None:
        if host and host not in self._history:
            self._history.insert(0, host)
            self._history = self._history[:20]
            if self.app:
                self.app.config.set("remote_tools.history", self._history)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_activate(self) -> None:
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
