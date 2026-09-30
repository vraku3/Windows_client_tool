import re
import subprocess
from typing import Dict, List, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QScrollArea, QTabWidget, QTableWidget, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header, set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.wifi_analyzer import wifi_profile_audit
import logging
logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

_NET_COLS = ["SSID", "Signal %", "Channel", "Band", "Security", "Authentication", "BSSID"]
_IFACE_COLS = ["Property", "Value"]
_PROFILE_COLS = ["Profile", "Rating", "Authentication", "Cipher", "Auto-connect", "Hidden SSID", "Has key"]


def _run_netsh(*args) -> str:
    result = subprocess.run(
        ["netsh", "wlan"] + list(args),
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=30,
    )
    return result.stdout


def _channel_to_band(ch: str) -> str:
    try:
        n = int(ch)
        return "2.4 GHz" if n <= 14 else "5 GHz"
    except ValueError:
        return ""


def parse_networks() -> List[Dict]:
    """Parse 'netsh wlan show networks mode=bssid' output into a list of dicts."""
    output = _run_netsh("show", "networks", "mode=bssid")
    networks: List[Dict] = []
    current_net: Dict = {}
    current_bssid: Dict = {}

    def _flush_bssid():
        if current_bssid and current_net:
            entry = dict(current_net)
            entry.update(current_bssid)
            ch = entry.get("Channel", "")
            entry["Band"] = _channel_to_band(ch)
            networks.append(entry)

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        m = re.match(r"SSID\s+\d+\s*:\s*(.*)", line)
        if m:
            _flush_bssid()
            current_bssid = {}
            current_net = {"SSID": m.group(1).strip()}
            continue

        m = re.match(r"Authentication\s*:\s*(.*)", line)
        if m and current_net and "BSSID" not in current_bssid:
            current_net["Authentication"] = m.group(1).strip()
            continue
        m = re.match(r"Encryption\s*:\s*(.*)", line)
        if m and current_net and "BSSID" not in current_bssid:
            current_net["Security"] = m.group(1).strip()
            continue

        m = re.match(r"BSSID\s+\d+\s*:\s*(.*)", line)
        if m:
            _flush_bssid()
            current_bssid = {"BSSID": m.group(1).strip()}
            continue

        m = re.match(r"Signal\s*:\s*(\d+)%", line)
        if m:
            current_bssid["Signal %"] = int(m.group(1))
            continue
        m = re.match(r"Channel\s*:\s*(\d+)", line)
        if m:
            current_bssid["Channel"] = m.group(1)
            continue

    _flush_bssid()
    return sorted(networks, key=lambda n: n.get("Signal %", 0), reverse=True)


def parse_interfaces() -> List[Dict]:
    """Parse 'netsh wlan show interfaces' for current connection details."""
    output = _run_netsh("show", "interfaces")
    interfaces: List[Dict] = []
    current: Dict = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current:
                interfaces.append(current)
                current = {}
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            current[key.strip()] = val.strip()
    if current:
        interfaces.append(current)
    return interfaces


def build_channel_map(networks: List[Dict]) -> Dict[str, Dict[int, int]]:
    """Return {band: {channel: count}} for channel congestion display."""
    result: Dict[str, Dict[int, int]] = {"2.4 GHz": {}, "5 GHz": {}}
    for n in networks:
        band = n.get("Band", "")
        ch_str = n.get("Channel", "")
        if band in result and ch_str:
            try:
                ch = int(ch_str)
                result[band][ch] = result[band].get(ch, 0) + 1
            except ValueError:
                logger.debug("Ignored ValueError", exc_info=True)
    return result


def _scan_all(_worker) -> Dict:
    networks = parse_networks()
    interfaces = parse_interfaces()
    channel_map = build_channel_map(networks)
    return {"networks": networks, "interfaces": interfaces, "channel_map": channel_map}


def _make_table(cols: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    center_header(t)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for i in range(1, len(cols)):
        t.horizontalHeader().setSectionResizeMode(
            i, QHeaderView.ResizeMode.ResizeToContents
        )
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return t


class WifiAnalyzerModule(BaseModule):
    name = "Wi-Fi Analyzer"
    icon = "📶"
    description = "Visible Wi-Fi networks, signal strength, and channel congestion"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._scan_worker: Optional[Worker] = None
        self._auto_refresh_timer: Optional[QTimer] = None
        # Declared here, not only in create_widget: on_stop() runs even for a
        # module whose widget was never built, which is the normal case for a
        # composite tab nobody opened.
        self._progress = None
        self._scan_btn = None
        self._profile_worker: Optional[Worker] = None
        self._profiles_loaded = False
        # Declared here too, for the same reason as `_progress`/`_scan_btn`
        # above: on_stop()/on_deactivate() can reach this module before its
        # widget was ever built (a composite tab nobody opened).
        self._profile_refresh_btn = None
        self._profile_status_lbl = None
        self._profile_table = None

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        self._scan_btn = QPushButton("🔍 Scan")
        self._scan_btn.setStyleSheet("font-weight: bold;")
        self._auto_refresh_cb = QCheckBox("Auto-refresh")
        self._auto_refresh_cb.stateChanged.connect(self._on_auto_refresh_changed)
        self._status_lbl = QLabel("Click Scan to discover networks.")
        toolbar.addWidget(self._scan_btn)
        toolbar.addWidget(self._auto_refresh_cb)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # Networks tab
        self._net_table = _make_table(_NET_COLS)
        tabs.addTab(self._net_table, "Networks")

        # Current connection tab
        self._iface_table = _make_table(_IFACE_COLS)
        tabs.addTab(self._iface_table, "Current Connection")

        # Channel map tab — with visual bar chart
        self._channel_widget = QWidget()
        ch_layout = QVBoxLayout(self._channel_widget)

        for band in ("2.4 GHz", "5 GHz"):
            band_lbl = QLabel(band)
            band_lbl.setStyleSheet("font-weight: bold;")
            ch_layout.addWidget(band_lbl)
            setattr(self, f"_ch_area_{band.replace(' ','_')}", QScrollArea())
            ch_area = getattr(self, f"_ch_area_{band.replace(' ','_')}")
            ch_area.setWidgetResizable(True)
            ch_area.setStyleSheet("border: none; background: transparent;")
            ch_area_widget = QWidget()
            setattr(self, f"_ch_content_{band.replace(' ','_')}", QVBoxLayout(ch_area_widget))
            ch_area.setWidget(ch_area_widget)
            ch_layout.addWidget(ch_area)

        ch_layout.addStretch()
        tabs.addTab(self._channel_widget, "Channel Map")

        # Saved Profiles tab -- what Windows REMEMBERS and will silently
        # rejoin, as opposed to the Networks tab above (what is currently
        # broadcasting). See wifi_profile_audit.py.
        self._profile_widget = QWidget()
        prof_layout = QVBoxLayout(self._profile_widget)
        prof_toolbar = QHBoxLayout()
        self._profile_refresh_btn = QPushButton("Refresh")
        self._profile_status_lbl = QLabel("")
        prof_toolbar.addWidget(self._profile_refresh_btn)
        prof_toolbar.addStretch()
        prof_toolbar.addWidget(self._profile_status_lbl)
        prof_layout.addLayout(prof_toolbar)
        self._profile_table = _make_table(_PROFILE_COLS)
        prof_layout.addWidget(self._profile_table, 1)
        self._profile_refresh_btn.clicked.connect(self._load_profiles)
        tabs.addTab(self._profile_widget, "Saved Profiles")

        self._scan_btn.clicked.connect(self._do_scan)
        self._widget = outer
        return outer

    # ── lifecycle ───────────────────────────────────────────────────────────

    def get_status_info(self) -> str:
        return "WiFi Analyzer"

    def get_refresh_interval(self) -> Optional[int]:
        return 15_000

    def refresh_data(self) -> None:
        self._do_scan()

    def on_activate(self) -> None:
        if not getattr(self, "_loaded", False):
            self._loaded = True
            self._do_scan()
        if not self._profiles_loaded:
            self._profiles_loaded = True
            self._load_profiles()

    def on_deactivate(self) -> None:
        self._stop_scan()
        self._stop_profile_load()
        if self._auto_refresh_timer:
            self._auto_refresh_timer.stop()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self._stop_scan()
        self._stop_profile_load()
        if self._auto_refresh_timer:
            self._auto_refresh_timer.stop()
        self.cancel_all_workers()

    # ── scan ────────────────────────────────────────────────────────────────

    def _do_scan(self):
        self._stop_scan()
        # refresh_data() reaches here from the auto-refresh timer, which can
        # tick before this module's tab has ever been opened — there is then
        # nothing to scan into.
        if self._widget is None:
            return
        self._scan_btn.setEnabled(False)
        self._status_lbl.setText("Scanning…")
        self._progress.show()

        self._scan_worker = Worker(_scan_all)
        self._scan_worker.signals.result.connect(self._on_result)
        self._scan_worker.signals.error.connect(self._on_error)
        self._scan_worker.signals.finished.connect(self._on_finished)
        self.app.thread_pool.start(self._scan_worker)

    def _stop_scan(self):
        if self._scan_worker is not None:
            self._scan_worker.cancel()
            self._scan_worker = None
        # on_stop() reaches here on shutdown, and as a tab of Network
        # Diagnostics this module's widget is built lazily — so there may be
        # no UI to put back. Cancelling the worker above is the part that
        # always has to happen.
        if self._progress is not None:
            self._progress.hide()
        if self._scan_btn is not None:
            self._scan_btn.setEnabled(True)

    def _on_finished(self):
        self._scan_btn.setEnabled(True)
        self._progress.hide()

    def _on_result(self, data: Dict):
        if not widget_is_valid(self._net_table):
            return
        networks = data["networks"]
        interfaces = data["interfaces"]
        channel_map = data["channel_map"]

        # ── Networks table ──────────────────────────────────────────────────
        self._net_table.setRowCount(len(networks))
        for r, net in enumerate(networks):
            sig = net.get("Signal %", 0)
            color = (
                QColor(semantic("success")) if sig >= 70
                else QColor(semantic("warning")) if sig >= 40
                else QColor(semantic("error"))
            )
            for c, col in enumerate(_NET_COLS):
                val = str(net.get(col, ""))
                item = centered_item(val)
                if col == "Signal %":
                    item.setForeground(color)
                self._net_table.setItem(r, c, item)

        # ── Current connection ─────────────────────────────────────────────
        iface_rows = []
        for iface in interfaces:
            for k, v in iface.items():
                iface_rows.append((k, v))
        self._iface_table.setRowCount(len(iface_rows))
        for r, (k, v) in enumerate(iface_rows):
            self._iface_table.setItem(r, 0, centered_item(k))
            self._iface_table.setItem(r, 1, centered_item(v))

        # ── Channel map ───────────────────────────────────────────────────
        for band in ("2.4_GHz", "5_GHz"):
            band_label = band.replace("_", " ")
            content_layout = getattr(self, f"_ch_content_{band}")
            # Clear old bars
            while content_layout.count():
                child = content_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            band_map = channel_map.get(band_label, {})
            if not band_map:
                content_layout.addWidget(
                    QLabel("<span style='color:#888'>No networks detected in this band.</span>")
                )
                continue
            max_count = max(band_map.values()) if band_map else 1
            for ch in sorted(band_map.keys()):
                count = band_map[ch]
                pct = count / max_count
                bar = QLabel()
                bar.setFixedHeight(18)
                bar.setStyleSheet(f"""
                    QLabel {{
                        background: qlineargradient(x1:0, x2:1, y1:0, y2:0,
                            stop:0 #4488FF, stop:{pct:.2f} #4488FF,
                            stop:{pct:.2f} #3c3c3c, stop:1 #3c3c3c);
                        border-radius: 3px;
                        padding: 2px 6px;
                        color: white;
                    }}
                """)
                bar.setText(f"  Ch {ch:3d}:  {count} network{'s' if count > 1 else ''}")
                content_layout.addWidget(bar)

        self._status_lbl.setText(f"{len(networks)} network(s) found — auto-refresh {self._auto_refresh_cb.isChecked() and 'ON' or 'OFF'}")

    def _on_error(self, err: str):
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"Error: {err}")

    def _on_auto_refresh_changed(self, state: int):
        enabled = state == Qt.CheckState.Checked.value
        if self._auto_refresh_timer is None:
            self._auto_refresh_timer = QTimer()
            self._auto_refresh_timer.timeout.connect(self._do_scan)
        if enabled:
            self._auto_refresh_timer.start(15_000)  # 15 seconds
        else:
            self._auto_refresh_timer.stop()

    # ── saved profile security audit ──────────────────────────────────────

    def _load_profiles(self):
        self._stop_profile_load()
        # Loaded lazily on first activation and again on demand from the
        # Refresh button -- unlike Networks, saved profiles rarely change,
        # so there is no auto-refresh timer for this tab.
        if self._widget is None:
            return
        if widget_is_valid(self._profile_refresh_btn):
            self._profile_refresh_btn.setEnabled(False)
        if widget_is_valid(self._profile_status_lbl):
            self._profile_status_lbl.setText("Reading saved Wi-Fi profiles…")

        self._profile_worker = Worker(lambda _w: wifi_profile_audit.audit_profiles())
        self._profile_worker.signals.result.connect(self._on_profiles_result)
        self._profile_worker.signals.error.connect(self._on_profiles_error)
        self.app.thread_pool.start(self._profile_worker)

    def _stop_profile_load(self):
        if self._profile_worker is not None:
            self._profile_worker.cancel()
            self._profile_worker = None
        if widget_is_valid(self._profile_refresh_btn):
            self._profile_refresh_btn.setEnabled(True)

    def _on_profiles_result(self, profiles):
        if not widget_is_valid(self._profile_table):
            return
        self._profile_refresh_btn.setEnabled(True)
        if profiles is None:
            self._profile_table.setRowCount(0)
            self._profile_status_lbl.setText("Could not read saved Wi-Fi profiles (netsh refused).")
            set_role(self._profile_status_lbl, "statusError")
            return

        self._profile_table.setRowCount(len(profiles))
        for r, p in enumerate(profiles):
            if p.refused:
                cells = [p.name, "Could not read", p.reason, "", "", "", ""]
                row_color = QColor(semantic("warning"))
            else:
                rating_label = {"open": "OPEN — no encryption", "wep": "WEP — crackable",
                                 "wpa": "WPA", "wpa2": "WPA2", "wpa3": "WPA3",
                                 "unknown": "Unrecognised"}.get(p.rating, p.rating)
                cells = [
                    p.name, rating_label,
                    ", ".join(p.auth_types) or "?",
                    ", ".join(p.ciphers) or "?",
                    "Yes" if p.auto_connect else ("No" if p.auto_connect is False else "?"),
                    "Yes" if p.hidden else ("No" if p.hidden is False else "?"),
                    "Yes" if p.has_key else ("No" if p.has_key is False else "?"),
                ]
                if p.rating in ("open", "wep"):
                    row_color = QColor(semantic("error"))
                elif p.hidden:
                    row_color = QColor(semantic("warning"))
                else:
                    row_color = None
            for c, val in enumerate(cells):
                item = centered_item(str(val))
                if row_color is not None:
                    item.setForeground(row_color)
                self._profile_table.setItem(r, c, item)

        risky = wifi_profile_audit.risky_profiles(profiles)
        hidden = wifi_profile_audit.hidden_profiles(profiles)
        unreadable = [p for p in profiles if p.refused]
        parts = [f"{len(profiles)} saved profile(s)"]
        if risky:
            parts.append(f"{len(risky)} with no real encryption (Open/WEP)")
        if hidden:
            parts.append(f"{len(hidden)} hidden-SSID (probes when out of range)")
        if unreadable:
            parts.append(f"{len(unreadable)} could not be read")
        self._profile_status_lbl.setText(" — ".join(parts))
        set_role(self._profile_status_lbl, "statusError" if (risky or unreadable) else
                 ("statusWarning" if hidden else "statusSuccess"))

    def _on_profiles_error(self, err: str):
        if widget_is_valid(self._profile_refresh_btn):
            self._profile_refresh_btn.setEnabled(True)
        if widget_is_valid(self._profile_status_lbl):
            self._profile_status_lbl.setText(f"Error: {err}")
            set_role(self._profile_status_lbl, "statusError")
