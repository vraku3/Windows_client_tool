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
)

from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.composite_module import CompositeModule
from core.events import NAV_REQUEST_MODULE, NavRequestData

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
            color = "#e06c75"
        elif pct >= 70:
            color = semantic("warning")
        else:
            color = "#98c379"
        self._bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 2px; }}"
        )
        self._val.setText(text)


# ---------------------------------------------------------------------------
# Dashboard widget
# ---------------------------------------------------------------------------


class _DashboardWidget(QWidget):
    _first_refresh_requested = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
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

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setSpacing(12)
        grid.setContentsMargins(12, 12, 12, 12)

        # --- System Info card (row 0, col 0-1) ---
        self._sys_card = _Card("System Information")
        self._os_lbl = QLabel("—")
        self._host_lbl = QLabel("—")
        self._cpu_name_lbl = QLabel("—")
        self._uptime_lbl = QLabel("—")
        self._boot_lbl = QLabel("—")
        for lbl in (
            self._os_lbl,
            self._host_lbl,
            self._cpu_name_lbl,
            self._uptime_lbl,
            self._boot_lbl,
        ):
            lbl.setWordWrap(True)
            self._sys_card.body().addWidget(lbl)
        grid.addWidget(self._sys_card, 0, 0, 1, 2)

        # --- CPU card (row 1, col 0) ---
        self._cpu_card = _Card("CPU")
        self._cpu_total = _StatBar("Total")
        self._cpu_card.body().addWidget(self._cpu_total)
        self._cpu_per_bars: list[_StatBar] = []
        grid.addWidget(self._cpu_card, 1, 0)

        # --- Memory card (row 1, col 1) ---
        self._mem_card = _Card("Memory")
        self._ram_bar = _StatBar("RAM")
        self._swap_bar = _StatBar("Swap / Page")
        self._mem_card.body().addWidget(self._ram_bar)
        self._mem_card.body().addWidget(self._swap_bar)
        self._mem_detail = QLabel("—")
        self._mem_detail.setStyleSheet("color: gray; font-size: 11px;")
        self._mem_card.body().addWidget(self._mem_detail)
        grid.addWidget(self._mem_card, 1, 1)

        # --- Disk card (row 2, col 0-1) ---
        self._disk_card = _Card("Disk Usage")
        self._disk_bars: dict[str, _StatBar] = {}
        grid.addWidget(self._disk_card, 2, 0, 1, 2)

        # --- Network card (row 3, col 0-1) ---
        self._net_card = _Card("Network (cumulative)")
        self._net_sent = QLabel("Sent: —")
        self._net_recv = QLabel("Received: —")
        self._net_card.body().addWidget(self._net_sent)
        self._net_card.body().addWidget(self._net_recv)
        grid.addWidget(self._net_card, 3, 0, 1, 2)

        # --- Driver Health card (row 4, col 0) ---
        # Read-only: shows Driver Manager's already-fetched driver list via
        # the module registry, never triggers a scan of its own.
        self._driver_card = _Card("Driver Health")
        self._driver_problems_lbl = QLabel("—")
        self._driver_card.body().addWidget(self._driver_problems_lbl)
        grid.addWidget(self._driver_card, 4, 0)

        grid.setRowStretch(5, 1)
        scroll.setWidget(inner)

        # --- Low disk space warning banner (hidden unless below threshold) ---
        self._space_banner = QFrame()
        self._space_banner.setStyleSheet("background: #FF8800; border-radius: 4px;")
        banner_lay = QHBoxLayout(self._space_banner)
        banner_lay.setContentsMargins(10, 6, 10, 6)
        self._space_banner_lbl = QLabel("")
        self._space_banner_lbl.setStyleSheet("color: white;")
        self._space_banner_lbl.setWordWrap(True)
        banner_lay.addWidget(self._space_banner_lbl, 1)
        self._clean_now_btn = QPushButton("Clean now")
        self._clean_now_btn.clicked.connect(self._on_clean_now)
        banner_lay.addWidget(self._clean_now_btn)
        self._space_banner.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._space_banner)

        # Refresh-rate slider: how fast the live numbers repaint.
        refresh_row = QHBoxLayout()
        refresh_row.setContentsMargins(10, 6, 10, 0)
        refresh_lbl = QLabel("Refresh rate")
        refresh_lbl.setStyleSheet("color: #888;")
        self._refresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._refresh_slider.setRange(1, 60)
        self._refresh_slider.setSingleStep(1)
        self._refresh_slider.setToolTip("How often the live metrics update")
        self._refresh_slider.valueChanged.connect(self._on_refresh_slider)
        self._refresh_val = QLabel("5 s")
        self._refresh_val.setMinimumWidth(36)
        self._refresh_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        refresh_row.addWidget(refresh_lbl)
        refresh_row.addWidget(self._refresh_slider, 1)
        refresh_row.addWidget(self._refresh_val)
        outer.addLayout(refresh_row)

        outer.addWidget(scroll)

    def _on_clean_now(self) -> None:
        if self.app is not None:
            self.app.event_bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name="Quick Cleanup"))

    def _refresh(self) -> None:
        if not _PSUTIL:
            return
        self._refresh_system()
        self._refresh_cpu()
        self._refresh_memory()
        self._refresh_disk()
        self._refresh_network()
        self._refresh_driver_health()

    def _refresh_system(self) -> None:
        from core.windows_utils import cpu_brand_name, windows_display_name
        self._os_lbl.setText(f"OS: {windows_display_name()}")
        self._host_lbl.setText(f"Host: {platform.node()}")
        cpu = (
            cpu_brand_name()
            or psutil.cpu_freq()
            and f"{psutil.cpu_freq().current:.0f} MHz"
        )
        self._cpu_name_lbl.setText(f"CPU: {cpu or '—'}")
        boot_ts = psutil.boot_time()
        boot_dt = datetime.fromtimestamp(boot_ts)
        uptime = datetime.now() - boot_dt
        h, rem = divmod(int(uptime.total_seconds()), 3600)
        m = rem // 60
        self._uptime_lbl.setText(f"Uptime: {h}h {m}m")
        self._boot_lbl.setText(f"Last boot: {boot_dt.strftime('%Y-%m-%d  %H:%M')}")

    def _refresh_cpu(self) -> None:
        total = psutil.cpu_percent(interval=None)
        self._cpu_total.update(total, f"{total:.1f}%")
        per = psutil.cpu_percent(percpu=True, interval=None)
        # Add per-core bars lazily
        while len(self._cpu_per_bars) < len(per):
            idx = len(self._cpu_per_bars)
            bar = _StatBar(f"Core {idx}")
            self._cpu_per_bars.append(bar)
            self._cpu_card.body().addWidget(bar)
        for i, pct in enumerate(per):
            self._cpu_per_bars[i].update(pct, f"{pct:.0f}%")

    def _refresh_memory(self) -> None:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        self._ram_bar.update(
            vm.percent, f"{vm.percent:.0f}%  ({_fmt(vm.used)}/{_fmt(vm.total)})"
        )
        self._swap_bar.update(
            sw.percent, f"{sw.percent:.0f}%  ({_fmt(sw.used)}/{_fmt(sw.total)})"
        )
        self._mem_detail.setText(
            f"Available: {_fmt(vm.available)}   Free: {_fmt(vm.free)}"
        )

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
                f"{usage.percent:.0f}%  ({_fmt(usage.used)}/{_fmt(usage.total)})",
            )

        # A volume that has gone away (USB pulled, card ejected) must lose its
        # bar. `seen` was collected and never read, so the card kept showing
        # the drive — frozen at whatever it read last — for the whole session.
        for label in [k for k in self._disk_bars if k not in seen]:
            bar = self._disk_bars.pop(label)
            self._disk_card.body().removeWidget(bar)
            bar.setParent(None)
            bar.deleteLater()

        self._refresh_space_warning()

    def _refresh_space_warning(self) -> None:
        if self.app is None:
            return
        try:
            system_drive = os.environ.get("SystemDrive", "C:") + "\\"
            usage = psutil.disk_usage(system_drive)
        except Exception:
            logger.warning("Ignored Exception in system-drive disk_usage", exc_info=True)
            return
        threshold_gb = self.app.config.get("app.free_space_warn_gb", 10)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < threshold_gb:
            self._space_banner_lbl.setText(
                f"Low disk space on {system_drive} — {free_gb:.1f} GB free "
                f"(warning threshold: {threshold_gb} GB)."
            )
            self._space_banner.show()
        else:
            self._space_banner.hide()

    def _refresh_network(self) -> None:
        io = psutil.net_io_counters()
        self._net_sent.setText(f"Sent:       {_fmt(io.bytes_sent)}")
        self._net_recv.setText(f"Received:  {_fmt(io.bytes_recv)}")

    def _refresh_driver_health(self) -> None:
        count = _driver_problem_count(self.app)
        if count is None:
            text = "Not scanned yet — open Driver Manager"
        elif count:
            text = f"{count} driver(s) need attention"
        else:
            text = "No driver problems detected"
        self._driver_problems_lbl.setText(text)

    def stop_timer(self) -> None:
        self._timer.stop()


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
            ProcessExplorerModule(),
            PerfMonModule(),
        ]
