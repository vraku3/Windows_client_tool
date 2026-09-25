"""Dashboard module displaying live system metrics.

Provides real-time monitoring of:
- CPU usage (total + per-core)
- Memory usage (RAM + swap)
- Disk usage (per volume)
- Network I/O (sent/recv)
- System uptime

Refresh interval: 3 seconds (configurable)
"""

import logging
import os
import platform
import time

from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.composite_module import CompositeModule
from core.events import NAV_REQUEST_MODULE, NavRequestData
from core.worker import Worker
from modules.dashboard.overview_health import (
    QUICK_TOOLS, History, collect_findings, identity_lines, launch_tool,
    network_summary, summary_text)
from modules.dashboard.overview_widgets import CoreGrid, FindingRow, MetricTile

logger = logging.getLogger(__name__)

try:
    import psutil

    def _fmt_bytes(bytes_count: int) -> str:
        """Convert bytes to human readable string."""
        if bytes_count == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        unit_index = 0
        while bytes_count >= 1024 and unit_index < len(units) - 1:
            bytes_count /= 1024
            unit_index += 1
        return f"{bytes_count:.1f} {units[unit_index]}"

    def _fmt_percent(percent: float, name="") -> str:
        """Format percentage with optional label."""
        if name:
            return f"{name}: {percent:.1f}%"
        return f"{percent:.1f}%"

    _PSUTIL = True
except ImportError:

    def _fmt_bytes(bytes_count: int) -> str:
        return str(bytes_count)

    def _fmt_percent(percent: float, name="") -> str:
        return str(percent)

    _PSUTIL = False


def _driver_problem_count(app) -> Optional[int]:
    """Reads Driver Manager's live, already-fetched driver list (the same
    _drivers_ref cell DriverSearchProvider already holds a live handle
    to -- see driver_module.py's get_search_provider) without triggering
    a new scan.

    None -- not 0 -- when there is nothing to report YET: Driver Manager
    isn't registered (e.g. in a test harness), or it is registered but
    hasn't scanned this session (`_drivers_ref[0]` is still the empty list
    `DriverModule.__init__` seeds it with). Driver Manager exposes no
    separate "have I ever scanned" flag, so an empty list is the best
    available signal for "not yet scanned" -- collapsing that into a real
    0 would render "No driver problems detected" for a machine nobody has
    actually looked at. 0 is still the real answer once a scan has
    actually returned at least one driver with no problems flagged."""
    if app is None or getattr(app, "module_registry", None) is None:
        return None
    driver_module = next(
        (m for m in app.module_registry.modules if m.name == "Driver Manager"),
        None)
    if driver_module is None:
        return None
    drivers = driver_module._drivers_ref[0]
    if not drivers:
        return None
    return sum(1 for d in drivers if d.error_code != 0 or not d.signed)


# ---------------------------------------------------------------------------
# Small reusable card widget
# ---------------------------------------------------------------------------


class _Card(QFrame):
    """Reusable card widget for dashboard metrics.

    Attributes:
        title: Card title text (auto-formatted)
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(12, 10, 12, 10)
        vbox.setSpacing(4)
        title_lbl = QLabel(title)
        font = title_lbl.font()
        font.setBold(True)
        _pt = font.pointSize()
        if _pt > 0:
            font.setPointSize(_pt - 1)
        title_lbl.setFont(font)
        title_lbl.setStyleSheet("color: gray;")
        vbox.addWidget(title_lbl)
        self._body = QVBoxLayout()
        self._body.setSpacing(6)
        vbox.addLayout(self._body)
        # Cards are stretched to their row's height; without this the extra
        # space is shared out between the title and the body, so titles floated.
        vbox.addStretch(1)

    def body(self) -> QVBoxLayout:
        return self._body


class _StatBar(QWidget):
    """Label + progress bar + value label in one row."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(0, 0, 0, 0)
        self._name = QLabel(label)
        self._name.setFixedWidth(110)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setFixedHeight(14)
        self._bar.setTextVisible(False)
        self._val = QLabel("—")
        # A MINIMUM, not a fixed width. The CPU rows put "2.8%" here and fit
        # in 60px, but the memory rows put "30%  (18.6 GB/61.6 GB)" here --
        # and a fixed 60px right-aligned label clips from the LEFT, so the
        # RAM row read "B/61.6 GB)" and the page-file row ") B/3.9 GB)".
        # Clipped text that still looks like text is worse than an ellipsis.
        self._val.setMinimumWidth(60)
        self._val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        hbox.addWidget(self._name)
        hbox.addWidget(self._bar, stretch=1)
        hbox.addWidget(self._val)

    def update(self, pct: float, text: str) -> None:
        self._bar.setValue(int(pct))
        # Color the bar based on usage level
        if pct >= 90:
            color = semantic("error")
        elif pct >= 70:
            color = semantic("warning")
        else:
            color = semantic("success")
        self._bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 2px; }}"
        )
        self._val.setText(text)


# ---------------------------------------------------------------------------
# Dashboard widget
# ---------------------------------------------------------------------------


class _DashboardWidget(QWidget):
    _first_refresh_requested = False
    FINDINGS_EVERY_S = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._workers: list = []
        self._histories = {k: History() for k in ("cpu", "mem", "disk", "net")}
        self._last_io = None
        self._last_findings_at = 0.0
        self._findings_busy = False
        self._top_busy = False
        self._findings: list = []
        self._top_source = None
        self._topology = None
        self._energy = None          # None: not tried; False: this hardware has no meter
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._refresh)
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app
        # The refresh slider controls how often the live pane repaints.
        # Persisted, so a slower laptop can ask for 10s and a demo can ask
        # for 1s without re-doing it every launch.
        seconds = 5
        if app is not None and getattr(app, "config", None) is not None:
            try:
                seconds = int(app.config.get("modules.dashboard.refresh_sec", 5))
            except (TypeError, ValueError):
                seconds = 5
        seconds = max(1, min(60, seconds))
        self._refresh_slider.setValue(seconds)
        self._on_refresh_slider(seconds)

    def _on_refresh_slider(self, seconds: int) -> None:
        self._timer.setInterval(max(1, min(60, seconds)) * 1000)
        self._refresh_val.setText(f"{seconds} s")
        if self.app is not None and getattr(self.app, "config", None) is not None:
            self.app.config.set("modules.dashboard.refresh_sec", int(seconds))

    def showEvent(self, event):
        super().showEvent(event)
        # Defer the first refresh until the widget is actually visible
        # to avoid wasting CPU on data the user hasn't seen yet
        if not _DashboardWidget._first_refresh_requested:
            _DashboardWidget._first_refresh_requested = True
            self._refresh()
            self._timer.start()

    # ---- layout -----------------------------------------------------------

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(12)
        col.addLayout(self._build_header())
        col.addLayout(self._build_tiles())
        col.addLayout(self._build_middle())
        col.addLayout(self._build_bottom())
        col.addWidget(self._build_network_card())
        col.addWidget(self._build_tools())
        col.addStretch(1)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _build_header(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        top = QHBoxLayout()
        self._host_lbl = QLabel(platform.node())
        font = self._host_lbl.font()
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        self._host_lbl.setFont(font)
        top.addWidget(self._host_lbl)
        top.addStretch(1)
        self._copy_btn = QPushButton("Copy summary")
        self._copy_btn.setToolTip("Copy a plain-text snapshot of this machine for a ticket")
        self._copy_btn.clicked.connect(self._copy_summary)
        top.addWidget(self._copy_btn)
        top.addSpacing(12)
        top.addWidget(self._build_refresh_control())
        box.addLayout(top)
        self._os_lbl = QLabel("—")
        self._os_lbl.setStyleSheet("color: gray;")
        self._os_lbl.setWordWrap(True)
        box.addWidget(self._os_lbl)
        return box

    def _build_refresh_control(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel("Refresh")
        label.setStyleSheet("color: gray;")
        self._refresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._refresh_slider.setRange(1, 60)
        self._refresh_slider.setFixedWidth(140)
        self._refresh_slider.setToolTip("How often the live metrics update")
        self._refresh_slider.valueChanged.connect(self._on_refresh_slider)
        self._refresh_val = QLabel("5 s")
        self._refresh_val.setMinimumWidth(34)
        for w in (label, self._refresh_slider, self._refresh_val):
            row.addWidget(w)
        return holder

    def _build_tiles(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._tiles = {
            "cpu": MetricTile("CPU"), "mem": MetricTile("Memory"),
            "disk": MetricTile("Disk activity", ceiling=50.0),
            "net": MetricTile("Network", ceiling=1.0),
        }
        for tile in self._tiles.values():
            row.addWidget(tile, 1)
        return row

    def _build_middle(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._attention_card = _Card("Needs attention")
        self._attention_box = QVBoxLayout()
        self._attention_box.setSpacing(4)
        self._attention_card.body().addLayout(self._attention_box)
        self._attention_status = QLabel("Checking…")
        self._attention_status.setStyleSheet("color: gray;")
        self._attention_card.body().addWidget(self._attention_status)
        recheck = QPushButton("Re-check now")
        recheck.clicked.connect(lambda: self._start_findings(force=True))
        self._attention_card.body().addWidget(recheck, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(self._attention_card, 3)
        self._top_card = _Card("Top consumers")
        self._top_cpu_lbl = QLabel("—")
        self._top_mem_lbl = QLabel("—")
        for text, lbl in (("By CPU", self._top_cpu_lbl), ("By memory", self._top_mem_lbl)):
            head = QLabel(text)
            head.setStyleSheet("color: gray; font-weight: bold;")
            self._top_card.body().addWidget(head)
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setStyleSheet("font-family: Consolas, monospace;")
            self._top_card.body().addWidget(lbl)
        self._top_card.body().addStretch(1)
        row.addWidget(self._top_card, 2)
        return row

    def _build_bottom(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._disk_card = _Card("Drives")
        self._disk_bars: dict[str, _StatBar] = {}
        row.addWidget(self._disk_card, 3)
        self._cores_card = _Card("CPU cores")
        self._core_grid = CoreGrid()
        self._cores_card.body().addWidget(self._core_grid)
        self._cores_card.body().addStretch(1)
        row.addWidget(self._cores_card, 2)
        return row

    def _build_network_card(self) -> QWidget:
        card = _Card("Network and identity")
        self._identity_lbl = QLabel("—")
        self._adapters_lbl = QLabel("—")
        for lbl in (self._identity_lbl, self._adapters_lbl):
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setStyleSheet("font-family: Consolas, monospace;")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card.body().addWidget(lbl)
        return card

    def _refresh_network_card(self) -> None:
        self._identity_lbl.setText("\n".join(identity_lines()))
        rows = network_summary()
        if rows is None:
            self._adapters_lbl.setText("Could not read the network adapters.")
        elif not rows:
            self._adapters_lbl.setText("No network adapter is up.")
        else:
            self._adapters_lbl.setText("\n".join(
                f"{name:<28} {ip or 'no address':<16} {speed:>6,} Mbit/s"
                if speed else f"{name:<28} {ip or 'no address':<16}"
                for name, ip, speed in rows))

    def _build_tools(self) -> QWidget:
        card = _Card("Quick tools")
        row = QHBoxLayout()
        row.setSpacing(6)
        for label, argv, tip in QUICK_TOOLS:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, a=argv: self._open_tool(a))
            row.addWidget(button)
        row.addStretch(1)
        card.body().addLayout(row)
        self._tools_status = QLabel("")
        self._tools_status.setStyleSheet("color: gray;")
        card.body().addWidget(self._tools_status)
        return card

    def _open_tool(self, argv) -> None:
        self._tools_status.setText(launch_tool(argv) or "")

    # ---- refresh ----------------------------------------------------------

    def _refresh(self) -> None:
        if not _PSUTIL:
            return
        self._refresh_system()
        self._refresh_cpu()
        self._refresh_memory()
        self._refresh_io()
        self._refresh_disk()
        self._refresh_network_card()
        self._start_top()
        self._start_findings()

    def _refresh_system(self) -> None:
        from core.windows_utils import cpu_brand_name, windows_display_name
        cpu = cpu_brand_name() or "CPU unknown"
        boot_dt = datetime.fromtimestamp(psutil.boot_time())
        secs = int((datetime.now() - boot_dt).total_seconds())
        self._uptime_text = _uptime_text(secs)
        self._cpu_text = cpu
        self._os_text = windows_display_name()
        self._os_lbl.setText(
            f"{self._os_text}   •   {cpu}   •   up {self._uptime_text}"
            f"   •   booted {boot_dt.strftime('%Y-%m-%d %H:%M')}")

    def _power_text(self) -> str:
        """"  •  76 W" from the CPU's energy meter, or nothing where there is none."""
        if self._energy is None:
            from modules.dashboard.energy import EnergyMeter
            try:
                self._energy = EnergyMeter()
            except OSError as e:
                logger.info("no energy meter for the Overview: %s", e)
                self._energy = False
        if not self._energy:
            return ""
        reading = self._energy.read()
        if reading is None:
            return ""
        watts = reading.package_w if reading.package_w is not None else reading.cores_total_w
        return f"  •  {watts:.0f} W"

    def _push(self, key: str, value: float) -> list:
        self._histories[key].add(value)
        return self._histories[key].values()

    def _refresh_cpu(self) -> None:
        total = psutil.cpu_percent(interval=None)
        per = psutil.cpu_percent(percpu=True, interval=None)
        freq = psutil.cpu_freq()
        cap = (f"{len(per)} logical cores" + (f"  •  {freq.current / 1000:.2f} GHz" if freq else "")
               + self._power_text())
        self._tiles["cpu"].show_reading(f"{total:.0f}%", cap, total, self._push("cpu", total))
        self._core_grid.set_loads(per)
        if self._topology is None:
            from modules.dashboard.topology import read_topology
            self._topology = read_topology() or False      # False: asked, no answer
        if self._topology:
            self._core_grid.set_kinds([self._topology.kind_of(i) for i in range(len(per))])

    def _refresh_memory(self) -> None:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        cap = (f"{_fmt(vm.used)} of {_fmt(vm.total)}  •  {_fmt(vm.available)} free"
               f"  •  page file {sw.percent:.0f}%")
        self._tiles["mem"].show_reading(f"{vm.percent:.0f}%", cap, vm.percent,
                                        self._push("mem", vm.percent))

    def _refresh_io(self) -> None:
        now = time.monotonic()
        try:
            disk, net = psutil.disk_io_counters(), psutil.net_io_counters()
        except Exception:
            logger.warning("io counters unreadable", exc_info=True)
            return
        last, self._last_io = self._last_io, (now, disk, net)
        if last is None or now <= last[0]:
            return
        dt = now - last[0]
        d_bps = ((disk.read_bytes - last[1].read_bytes) + (disk.write_bytes - last[1].write_bytes)) / dt
        rx = (net.bytes_recv - last[2].bytes_recv) / dt
        tx = (net.bytes_sent - last[2].bytes_sent) / dt
        disk_mb = d_bps / 1048576
        self._tiles["disk"].show_reading(
            f"{disk_mb:.1f} MB/s",
            f"read {_fmt(disk.read_bytes)} • written {_fmt(disk.write_bytes)} since boot",
            min(100.0, disk_mb * 2), self._push("disk", disk_mb))
        self._tiles["net"].show_reading(
            f"↓ {_rate(rx)}   ↑ {_rate(tx)}",
            f"received {_fmt(net.bytes_recv)} • sent {_fmt(net.bytes_sent)} since boot",
            0.0, self._push("net", (rx + tx) / 1048576))

    def _refresh_disk(self) -> None:
        try:
            parts = psutil.disk_partitions(all=False)
        except Exception:
            logger.warning("Ignored Exception in disk_partitions", exc_info=True)
            return
        seen: set[str] = set()
        for p in _mounted_partitions(parts):
            try:
                usage = psutil.disk_usage(p.mountpoint)
            except Exception:
                logger.warning("Ignored Exception in disk_usage for %s", p.mountpoint, exc_info=True)
                continue
            label = f"{p.device}  [{p.fstype}]"
            seen.add(label)
            if label not in self._disk_bars:
                bar = _StatBar(p.device[:20])
                self._disk_bars[label] = bar
                self._disk_card.body().addWidget(bar)
            self._disk_bars[label].update(
                usage.percent,
                f"{usage.percent:.0f}%  ({_fmt(usage.free)} free of {_fmt(usage.total)})",
            )

        # A volume that has gone away (USB pulled, card ejected) must lose its
        # bar. `seen` was collected and never read, so the card kept showing
        # the drive — frozen at whatever it read last — for the whole session.
        for label in [k for k in self._disk_bars if k not in seen]:
            bar = self._disk_bars.pop(label)
            self._disk_card.body().removeWidget(bar)
            bar.setParent(None)
            bar.deleteLater()

    # ---- background reads -------------------------------------------------

    def _pool(self):
        return getattr(self.app, "thread_pool", None) if self.app is not None else None

    def _start_top(self) -> None:
        pool = self._pool()
        if pool is None or self._top_busy:
            return
        self._top_busy = True
        if self._top_source is None:
            from core.procengine.snapshot import SnapshotSource
            self._top_source = SnapshotSource()
        worker = Worker(lambda _w: _top_consumers(self._top_source.read()))
        worker.signals.result.connect(self._on_top)
        worker.signals.error.connect(self._on_top_error)
        self._workers.append(worker)
        pool.start(worker)

    def _on_top_error(self, message) -> None:
        self._top_busy = False
        logger.warning("top consumers unreadable: %s", message)
        self._top_cpu_lbl.setText("Could not read the process list")
        self._top_mem_lbl.setText("")

    def _on_top(self, result) -> None:
        self._top_busy = False
        if not _alive(self):
            return
        by_cpu, by_mem = result
        self._top_cpu_lbl.setText("\n".join(f"{c:5.1f}%   {n}" for n, c, _ in by_cpu)
                                  or "Measuring…")
        self._top_mem_lbl.setText("\n".join(f"{_fmt(m):>10}   {n}" for n, _, m in by_mem))

    def _start_findings(self, force: bool = False) -> None:
        pool = self._pool()
        if pool is None or self._findings_busy:
            return
        if not force and time.monotonic() - self._last_findings_at < self.FINDINGS_EVERY_S:
            return
        self._findings_busy = True
        drivers = _driver_problem_count(self.app)
        worker = Worker(lambda _w: collect_findings(driver_problems=drivers))
        worker.signals.result.connect(self._on_findings)
        worker.signals.error.connect(self._on_findings_error)
        self._workers.append(worker)
        pool.start(worker)

    def _on_findings_error(self, message) -> None:
        self._findings_busy = False
        logger.warning("health checks failed: %s", message)
        self._attention_status.setText(f"The health checks failed: {message}")

    def _on_findings(self, findings) -> None:
        self._findings_busy = False
        if not _alive(self):
            return
        self._last_findings_at = time.monotonic()
        self._findings = findings
        while self._attention_box.count():
            item = self._attention_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for finding in findings:
            row = FindingRow(finding)
            row.action_requested.connect(self._navigate)
            self._attention_box.addWidget(row)
        real = [f for f in findings if f.severity != "unknown"]
        self._attention_status.setText(
            "Nothing needs attention." if not findings else
            f"{len(real)} item(s) need attention." if real else "")

    def _navigate(self, module_name: str) -> None:
        if self.app is not None:
            self.app.event_bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name=module_name))

    def _copy_summary(self) -> None:
        vm = psutil.virtual_memory()
        lines = [f"RAM: {vm.percent:.0f}% ({_fmt(vm.used)} of {_fmt(vm.total)})"]
        lines += [f"Drive {label.split()[0]}: {bar._val.text()}"
                  for label, bar in self._disk_bars.items()]
        text = summary_text(platform.node(), getattr(self, "_os_text", ""),
                            getattr(self, "_cpu_text", ""), getattr(self, "_uptime_text", ""),
                            self._findings, lines)
        QApplication.clipboard().setText(text)
        self._copy_btn.setText("Copied ✓")
        QTimer.singleShot(1500, lambda: _alive(self) and self._copy_btn.setText("Copy summary"))

    def stop_timer(self) -> None:
        self._timer.stop()
        if self._energy:
            self._energy.close()
            self._energy = None
        for w in self._workers:
            w.cancel()
        self._workers.clear()


def _alive(widget) -> bool:
    try:
        from PyQt6 import sip
        return not sip.isdeleted(widget)
    except ImportError:
        return True


def _uptime_text(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    return (f"{days}d " if days else "") + f"{hours}h {rem // 60}m"


def _rate(bytes_per_s: float) -> str:
    return f"{_fmt(bytes_per_s)}/s"


def _top_consumers(snapshot, count: int = 5):
    """(name, cpu %, private bytes) for the busiest and the largest processes."""
    rows = []
    for info in snapshot.by_pid.values():
        if info.pid == 0:
            continue
        cpu = info.rates.cpu_percent
        rows.append((info.name, cpu or 0.0, info.raw.working_set_private))
    by_cpu = [r for r in sorted(rows, key=lambda r: r[1], reverse=True)[:count] if r[1] > 0]
    by_mem = sorted(rows, key=lambda r: r[2], reverse=True)[:count]
    return by_cpu, by_mem


def _mounted_partitions(parts):
    """Drop the partitions there is nothing to measure on.

    An empty card-reader slot is a real volume with no media in it: psutil
    lists it with an EMPTY fstype, and `disk_usage` on it raises
    PermissionError [WinError 21] "The device is not ready". Probing one every
    refresh put two tracebacks in the log every 3 seconds on this machine.
    Empty fstype is psutil's own signal for that, and it is the same guard
    `system_report` has always used.
    """
    return [p for p in parts if p.fstype and "cdrom" not in p.opts]


def _fmt(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class OverviewModule(BaseModule):
    """The at-a-glance pane the Dashboard used to be, now its first tab."""

    name = "Overview"
    icon = "🏠"
    description = "Live system overview — CPU, memory, disk, network, uptime"
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self):
        super().__init__()
        self._widget: _DashboardWidget | None = None
        self._refreshing = False

    def create_widget(self) -> QWidget:
        if not _PSUTIL:
            lbl = QLabel(
                "psutil is not installed.\n\n"
                "Run:  pip install psutil\n\nThen restart the application."
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            return lbl
        self._widget = _DashboardWidget()
        self._widget.set_app(self.app)
        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        if self._widget:
            self._widget.stop_timer()

    def on_activate(self) -> None:
        if self._widget and not self._refreshing:
            self._widget._timer.start()

    def on_deactivate(self) -> None:
        if self._widget:
            self._widget._timer.stop()

    def get_refresh_interval(self) -> Optional[int]:
        """The slider-driven rate, persisted under modules.dashboard.refresh_sec."""
        seconds = 5
        if self.app is not None and getattr(self.app, "config", None) is not None:
            try:
                seconds = int(self.app.config.get("modules.dashboard.refresh_sec", 5))
            except (TypeError, ValueError):
                seconds = 5
        return max(1, min(60, seconds)) * 1000

    def refresh_data(self) -> None:
        if self._refreshing or not self._widget:
            return
        self._refreshing = True
        self._widget._refresh()
        self._refreshing = False

    def get_status_info(self) -> str:
        return "Overview"


class DashboardModule(CompositeModule):
    """Task Manager and Process Explorer, in one place.

    Hosts the old at-a-glance pane as its first tab, then the process views.
    Process Explorer is a CHILD here rather than its own sidebar entry: two
    doors to the same room is two places to kill a process from, and two
    process engines to keep honest.
    """

    name = "Dashboard"
    icon = "🏠"
    description = ("System overview, every process, and Process Explorer's "
                   "detail")
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        # Imported here rather than at module scope, per CLAUDE.md: a child
        # imported at the top would be loaded even when the tab is never
        # opened, and the frozen build needs each one in HIDDEN_IMPORTS.
        from modules.dashboard.app_history_module import AppHistoryModule
        from modules.dashboard.details_module import DetailsModule
        from modules.dashboard.performance_module import PerformanceModule
        from modules.dashboard.processes_module import ProcessesModule
        from modules.dashboard.users_module import UsersModule
        from modules.dashboard.startup_module import StartupModule
        from modules.dashboard.services_module import ServicesModule
        from modules.dashboard.connections_tab import ConnectionsModule
        from modules.dashboard.sysinfo_tab import SystemInfoModule
        from modules.dashboard.installed_apps_tab import InstalledAppsModule
        from modules.dashboard.disk_space_tab import DiskSpaceModule
        from modules.dashboard.power_tab import PowerModule
        from modules.dashboard.benchmarks_tab import BenchmarksModule
        from modules.dashboard.flight_tab import FlightModule
        from modules.dashboard.energy_tab import EnergyModule
        from modules.dashboard.thermal_tab import ThermalModule
        from modules.perfmon.perfmon_module import PerfMonModule
        from modules.process_explorer.process_explorer_module import (
            ProcessExplorerModule)

        self.children = [
            OverviewModule(),
            ProcessesModule(),
            PerformanceModule(),
            DetailsModule(),
            UsersModule(),
            AppHistoryModule(),
            StartupModule(),
            ServicesModule(),
            ConnectionsModule(),
            SystemInfoModule(),
            InstalledAppsModule(),
            DiskSpaceModule(),
            PowerModule(),
            BenchmarksModule(),
            FlightModule(),
            EnergyModule(),
            ThermalModule(),
            ProcessExplorerModule(),
            PerfMonModule(),
        ]
