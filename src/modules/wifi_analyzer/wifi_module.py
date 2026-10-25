"""Wi-Fi Analyzer pane: networks, the current connection, channel map and
connection history.

The reading lives in the Qt-free engines beside this file --
``wifi_scan.py`` (netsh, refusal classification, 2.4 GHz overlap) and
``wlan_events.py`` (the WLAN AutoConfig log) -- and this file only shows it.
"""
from typing import Dict, List, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QScrollArea, QTabWidget, QTableWidget, QVBoxLayout, QWidget,
)

from ui.module_intro import module_intro
from ui.help.network_text import INTRO
from ui.help.network_ui import explain, explain_tabs, item_help, table_page, interface_property_help

from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, describe_table, set_role
from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from ui.table_style import apply_table_style
from ui.help.network_overview import add_glance, unreadable
from modules.wifi_analyzer import wifi_profile_audit, wifi_scan, wlan_events
from ui.error_banner import ErrorBanner
import logging
logger = logging.getLogger(__name__)

_NET_COLS = ["SSID", "Signal %", "Channel", "Band", "Radio", "Security",
             "Authentication", "Utilization %", "BSSID"]
_IFACE_COLS = ["Property", "Value"]
_PROFILE_COLS = ["Profile", "Rating", "Authentication", "Cipher", "Auto-connect", "Hidden SSID", "Has key"]
_HIST_COLS = ["Event", "Network", "Reason (Windows' words)", "Count",
              "Last seen", "What it means"]


def _scan_all(_worker) -> Dict:
    result = wifi_scan.scan()
    history = wlan_events.read_history()
    return {"scan": result, "history": history}


def _make_table(cols: List[str], stretch: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    apply_table_style(t, numeric_columns=[i for i, c in enumerate(cols)
                                         if c in ('Signal %', 'Channel', 'Utilization %', 'Count')],
                      date_columns=[i for i, c in enumerate(cols) if c == 'Last seen'])
    for i in range(len(cols)):
        t.horizontalHeader().setSectionResizeMode(
            i, QHeaderView.ResizeMode.Stretch if i == stretch
            else QHeaderView.ResizeMode.ResizeToContents)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    describe_table(t)
    return t


def _channel_line(ch: int, count: int, load: Optional[wifi_scan.ChannelLoad]) -> str:
    text = f"Ch {ch:3d}:  {count} network{'s' if count != 1 else ''}"
    if load is not None and load.adjacent:
        names = ", ".join(f"{n.get('SSID')} ch {n.get('Channel')}" for n in load.adjacent)
        text += f"  + {len(load.adjacent)} overlapping ({names})"
    if load is not None and ch not in wifi_scan.CLEAN_24GHZ_CHANNELS:
        text += "  -- not 1/6/11"
    return text


def _advice_text(advice: Optional[wifi_scan.ChannelAdvice]) -> str:
    if advice is None:
        return ""
    others = "; ".join(f"ch {c}: {n} overlapping, weight {s}"
                       for c, s, n in advice.ranking[1:])
    return (f"Least contended of 1/6/11: channel {advice.channel} "
            f"({advice.overlapping} overlapping BSSID(s), signal weight "
            f"{advice.score}). {others}. Assumes 20 MHz channels "
            f"(netsh does not report width).")


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
        self._net_table = None
        self._band_layouts: Dict[str, QVBoxLayout] = {}
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
        layout.addWidget(module_intro(INTRO["wifi"]))
        layout.setContentsMargins(8, 8, 8, 8)
        self._build_connection_card(layout)
        layout.addLayout(self._build_toolbar())

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._banner = ErrorBanner()
        layout.addWidget(self._banner)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self._net_table = _make_table(_NET_COLS)
        tabs.addTab(table_page(self._net_table, "Networks"), "Networks")
        tabs.addTab(self._build_iface_tab(), "Current Connection")
        tabs.addTab(self._build_channel_tab(), "Channel Map")
        tabs.addTab(self._build_history_tab(), "Connection History")
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
        explain_tabs(tabs, "wifi")
        explain(outer, "wifi", intro=False)
        return outer

    def _build_connection_card(self, layout):
        self._glance = add_glance(layout, [
            ('ssid', 'Current connection', 'info', 'SSID on the first connected Wi-Fi interface. Ethernet connections are not shown here.'),
            ('signal', 'Signal', 'info', 'Current Wi-Fi signal quality reported by Windows, not a throughput measurement.'),
            ('band', 'Band and channel', 'info', 'Band and channel of the connected access point, as reported by Windows.'),
            ('congestion', 'Competing radios', 'warning', 'Other visible BSSIDs on this channel; 2.4 GHz includes overlapping channels assuming 20 MHz. This is not measured airtime utilization.'),
        ])
        self._signal_bar = QProgressBar()
        self._signal_bar.setRange(0, 100)
        self._signal_bar.setFixedHeight(18)
        self._signal_bar.setFormat('Current connection signal: %p%')
        self._signal_bar.setToolTip('Signal quality from the connected interface. Hidden when Windows cannot report the signal.')
        self._signal_bar.hide()
        layout.addWidget(self._signal_bar)

    def _show_connection(self, scan):
        self._signal_bar.hide()
        if scan.interface_problem is not None:
            unreadable(self._glance, scan.interface_problem.message)
            return
        connected = [i for i in scan.interfaces if i.get('State', '').lower() == 'connected']
        if not connected:
            unreadable(self._glance, 'No connected Wi-Fi interface')
            self._glance['ssid'].set('Disconnected', f'{len(scan.interfaces)} Wi-Fi interface(s)', severity='muted')
            return
        iface = connected[0]
        self._glance['ssid'].set(iface.get('SSID') or None, iface.get('Name', 'Connected'),
                                 reason='Windows did not report the connected SSID')
        signal = str(iface.get('Signal', '')).strip().rstrip('%').strip()
        quality = int(signal) if signal.isdigit() and 0 <= int(signal) <= 100 else None
        self._glance['signal'].set(f'{quality}%' if quality is not None else None,
                                   'Strong' if quality is not None and quality >= 70 else 'Fair' if quality is not None and quality >= 40 else 'Weak',
                                   reason='Windows did not report signal quality',
                                   severity='ok' if quality is not None and quality >= 70 else 'warning')
        if quality is not None:
            self._signal_bar.setValue(quality)
            self._signal_bar.show()
        own = next((n for n in scan.networks if str(n.get('BSSID', '')).lower() == iface.get('BSSID', '').lower()
                    and iface.get('BSSID')), {})
        channel = str(iface.get('Channel') or own.get('Channel') or '')
        band = iface.get('Band') or own.get('Band') or wifi_scan.channel_to_band(channel)
        self._glance['band'].set(band or None, 'Channel ' + channel if channel else 'Channel not reported',
                                 reason='Windows did not report the band')
        self._show_congestion(scan, iface, band, channel)

    def _show_congestion(self, scan, iface, band, channel):
        if scan.problem is not None or not band or not channel.isdigit() or not iface.get('BSSID'):
            reason = scan.problem.message if scan.problem else 'Channel, band or connected BSSID unavailable'
            self._glance['congestion'].set_unreadable(reason)
            return
        own = iface['BSSID'].lower()
        distance = 4 if band == '2.4 GHz' else 0
        radios = {str(n['BSSID']).lower() for n in scan.networks
                  if n.get('BSSID') and str(n['BSSID']).lower() != own and n.get('Band') == band
                  and str(n.get('Channel', '')).isdigit()
                  and abs(int(n['Channel']) - int(channel)) <= distance}
        self._glance['congestion'].set(len(radios), 'Overlapping BSSIDs' if distance else 'Same-channel BSSIDs',
                                       severity='warning' if radios else 'ok')

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        self._scan_btn = QPushButton("🔍 Scan")
        self._auto_refresh_cb = QCheckBox("Auto-refresh")
        self._auto_refresh_cb.stateChanged.connect(self._on_auto_refresh_changed)
        self._status_lbl = QLabel("Click Scan to discover networks.")
        set_role(self._status_lbl, "muted")
        toolbar.addWidget(self._scan_btn)
        toolbar.addWidget(self._auto_refresh_cb)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        return toolbar

    def _build_iface_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self._iface_note = QLabel()
        self._iface_note.setWordWrap(True)
        self._iface_note.hide()
        lay.addWidget(self._iface_note)
        self._iface_table = _make_table(_IFACE_COLS, stretch=1)
        lay.addWidget(self._iface_table, 1)
        return page

    def _build_channel_tab(self) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        page = QWidget()
        ch_layout = QVBoxLayout(page)
        for band in wifi_scan.BANDS:
            band_lbl = QLabel(band)
            set_role(band_lbl, "heading")
            ch_layout.addWidget(band_lbl)
            if band == "2.4 GHz":
                self._advice_lbl = QLabel()
                self._advice_lbl.setWordWrap(True)
                set_role(self._advice_lbl, "statusInfo")
                ch_layout.addWidget(self._advice_lbl)
            box = QVBoxLayout()
            ch_layout.addLayout(box)
            self._band_layouts[band] = box
        ch_layout.addStretch()
        area.setWidget(page)
        return area

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self._hist_headline = QLabel("The WLAN AutoConfig log is read on Scan.")
        self._hist_headline.setWordWrap(True)
        lay.addWidget(self._hist_headline)
        self._hist_per_ssid = QLabel()
        self._hist_per_ssid.setWordWrap(True)
        set_role(self._hist_per_ssid, "muted")
        lay.addWidget(self._hist_per_ssid)
        # One line per row; the full explanation of the selected row goes in
        # the detail label below. Word-wrapped rows came out ~130 px tall,
        # four to a screen.
        self._hist_table = _make_table(_HIST_COLS, stretch=5)
        self._hist_table.setWordWrap(False)
        self._hist_table.currentCellChanged.connect(
            lambda row, *_: self._show_history_detail(row))
        lay.addWidget(self._hist_table, 1)
        self._hist_detail = QLabel()
        self._hist_detail.setWordWrap(True)
        self._hist_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._hist_detail)
        self._hist_groups: List[wlan_events.EventGroup] = []
        return page

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

        worker = Worker(_scan_all)
        self._scan_worker = worker
        current = lambda: self._scan_worker is worker and widget_is_valid(self._widget)
        worker.signals.result.connect(lambda data: self._on_result(data) if current() else None)
        worker.signals.error.connect(lambda err: self._on_error(err) if current() else None)
        worker.signals.finished.connect(lambda: self._on_finished() if current() else None)
        self._track_read(worker)

    def _track_read(self, worker):
        self._workers.append(worker)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self.app.thread_pool.start(worker)

    def _stop_scan(self):
        if self._scan_worker is not None:
            retire([self._scan_worker])
            self._scan_worker = None
        # on_stop() reaches here on shutdown, and as a tab of Network
        # Diagnostics this module's widget is built lazily — so there may be
        # no UI to put back. Cancelling the worker above is the part that
        # always has to happen.
        if widget_is_valid(self._progress):
            self._progress.hide()
        if widget_is_valid(self._scan_btn):
            self._scan_btn.setEnabled(True)

    def _on_finished(self):
        if not widget_is_valid(self._scan_btn):
            return
        self._scan_btn.setEnabled(True)
        self._progress.hide()

    def _on_result(self, data: Dict):
        if not widget_is_valid(self._net_table):
            return
        scan: wifi_scan.ScanResult = data["scan"]
        self._show_connection(scan)
        self._show_networks(scan)
        self._show_interfaces(scan)
        self._show_channel_map(scan)
        self._show_history(data["history"])

    # ── rendering ───────────────────────────────────────────────────────────

    def _show_networks(self, scan: wifi_scan.ScanResult) -> None:
        networks = scan.networks
        self._net_table.setRowCount(len(networks))
        for r, net in enumerate(networks):
            sig = net.get("Signal %", 0)
            color = (
                QColor(semantic("success")) if sig >= 70
                else QColor(semantic("warning")) if sig >= 40
                else QColor(semantic("error"))
            )
            for c, col in enumerate(_NET_COLS):
                val = net.get(col, "")
                item = centered_item("" if val is None else str(val))
                if col == "Signal %":
                    item.setForeground(color)
                if col == "Utilization %" and val == "":
                    item.setToolTip("This access point does not advertise BSS Load.")
                self._net_table.setItem(r, c, item_help(item, self._net_table, c))

        refresh = "ON" if self._auto_refresh_cb.isChecked() else "OFF"
        if scan.problem is not None:
            self._banner.set_error(scan.problem.message)
            self._banner.setToolTip(f"netsh said: {scan.problem.detail}")
            self._status_lbl.setText(f"Scan refused or unavailable ({scan.problem.kind})")
            set_role(self._status_lbl, "statusError")
        else:
            self._banner.clear()
            self._status_lbl.setText(
                f"{len(networks)} BSSID(s) found — auto-refresh {refresh}")
            set_role(self._status_lbl, "muted")

    def _show_interfaces(self, scan: wifi_scan.ScanResult) -> None:
        rows = [(k, v) for iface in scan.interfaces for k, v in iface.items()]
        self._iface_table.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            for c, text in enumerate((k, v)):
                item = centered_item(text)
                item.setToolTip(interface_property_help(k, str(v)))
                self._iface_table.setItem(r, c, item)
        note, role = "", "statusWarning"
        if scan.interface_problem is not None:
            note, role = scan.interface_problem.message, "statusError"
        else:
            off = []
            for iface in scan.interfaces:
                problem = wifi_scan.radio_problem(iface)
                if problem:
                    off.append(f"{iface.get('Name', '?')}: radio {problem}")
            if off:
                note = "Radio switched off — " + "; ".join(off)
        self._iface_note.setText(note)
        set_role(self._iface_note, role)
        self._iface_note.setVisible(bool(note))

    def _show_channel_map(self, scan: wifi_scan.ScanResult) -> None:
        channel_map = wifi_scan.build_channel_map(scan.networks)
        overlap = wifi_scan.overlap_report_24ghz(scan.networks)
        mine = wifi_scan.connected_ssids(scan.interfaces)
        self._advice_lbl.setText(_advice_text(
            wifi_scan.recommend_24ghz_channel(scan.networks, mine)))
        for band, box in self._band_layouts.items():
            while box.count():
                child = box.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            band_map = channel_map.get(band, {})
            if not band_map:
                empty = QLabel("Scan unavailable." if scan.problem
                               else "No networks detected in this band.")
                empty.setToolTip("No channel rows were returned for this band; an unavailable scan is not evidence that no access points exist.")
                set_role(empty, "muted")
                box.addWidget(empty)
                continue
            loads = {ch: overlap.get(ch) if band == "2.4 GHz" else None for ch in band_map}
            # One scale per band, so bar lengths compare across channels.
            peak = max(load.total if load is not None else band_map[ch]
                       for ch, load in loads.items())
            for ch in sorted(band_map):
                box.addWidget(self._channel_bar(ch, band_map[ch], peak, loads[ch]))

    @staticmethod
    def _channel_bar(ch: int, count: int, peak: int,
                     load: Optional[wifi_scan.ChannelLoad]) -> QProgressBar:
        bar = QProgressBar()
        total = load.total if load is not None else count
        bar.setRange(0, max(peak, 1))
        bar.setValue(total)
        bar.setFixedHeight(20)
        bar.setTextVisible(True)
        bar.setFormat(_channel_line(ch, count, load))
        bar.setToolTip("Count of visible access-point radios on this channel, including nearby overlapping channels for 2.4 GHz. Bar length is relative to the busiest channel in this band, not a signal percentage or airtime measurement; overlap assumes a narrow channel because netsh does not report width. Changing channels must be done on your router; this map only reads the scan.")
        bar.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _show_history(self, history: wlan_events.HistoryResult) -> None:
        if not history.readable:
            self._hist_headline.setText(f"Could not read the connection history: {history.error}")
            set_role(self._hist_headline, "statusError")
            self._hist_per_ssid.setText("")
            self._hist_groups = []
            self._hist_table.setRowCount(0)
            self._hist_detail.setText("")
            return
        self._hist_headline.setText(wlan_events.headline(history.events))
        set_role(self._hist_headline, "statusInfo")
        per_ssid = []
        for s in wlan_events.summarise_by_ssid(history.events)[:6]:
            rate = wlan_events.failure_rate(s)
            rate_text = f", {rate:.0%} of attempts failed" if rate is not None else ""
            per_ssid.append(f"{s.ssid}: {s.connected} connected, {s.failed} failed, "
                            f"{s.disconnected} disconnected{rate_text}")
        self._hist_per_ssid.setText("   •   ".join(per_ssid))
        groups = wlan_events.group_problems(history.events)
        self._hist_groups = groups
        self._hist_table.setRowCount(len(groups))
        for r, g in enumerate(groups):
            codes = ", ".join(g.reason_codes)
            cells = [g.kind, g.ssid, g.reason, str(g.count),
                     g.last.astimezone().strftime("%Y-%m-%d %H:%M"), g.meaning]
            for c, text in enumerate(cells):
                item = centered_item(text)
                if c == 2 and codes:
                    item.setToolTip(f"ReasonCode {codes}")
                if c == 5 and text:
                    item.setToolTip(text)
                if c == 0 and g.kind == "Connection failed" and not g.meaning:
                    item.setForeground(QColor(semantic("error")))
                self._hist_table.setItem(r, c, item_help(item, self._hist_table, c))
        row = self._hist_table.currentRow()
        self._show_history_detail(row if 0 <= row < len(groups) else 0)

    def _show_history_detail(self, row: int) -> None:
        if not (0 <= row < len(self._hist_groups)):
            self._hist_detail.setText("")
            return
        g = self._hist_groups[row]
        first = g.first.astimezone().strftime("%Y-%m-%d %H:%M")
        last = g.last.astimezone().strftime("%Y-%m-%d %H:%M")
        codes = f"  ReasonCode {', '.join(g.reason_codes)}." if g.reason_codes else ""
        meaning = g.meaning or "No known cause beyond Windows' own wording."
        self._hist_detail.setText(
            f"{g.kind} — {g.ssid}: {g.count}x between {first} and {last}.{codes}\n"
            f"Windows: {g.reason}\nMeaning: {meaning}")

    def _on_error(self, err: str):
        if not widget_is_valid(self._scan_btn):
            return
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        self._banner.set_error(f"Scan failed: {err}")
        self._status_lbl.setText("Scan failed")
        unreadable(self._glance, str(err))
        self._signal_bar.hide()
        set_role(self._status_lbl, "statusError")

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

        worker = Worker(lambda _w: wifi_profile_audit.audit_profiles())
        self._profile_worker = worker
        current = lambda: self._profile_worker is worker and widget_is_valid(self._widget)
        worker.signals.result.connect(lambda data: self._on_profiles_result(data) if current() else None)
        worker.signals.error.connect(lambda err: self._on_profiles_error(err) if current() else None)
        self._track_read(worker)

    def _stop_profile_load(self):
        if self._profile_worker is not None:
            retire([self._profile_worker])
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
                self._profile_table.setItem(r, c, item_help(item, self._profile_table, c))

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
