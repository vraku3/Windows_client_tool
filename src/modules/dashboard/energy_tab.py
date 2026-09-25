"""Energy: package and per-core power from the CPU's energy meter, plus which
processes are probably responsible (clearly labelled as an estimate)."""
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHeaderView, QLabel, QTableWidget, QVBoxLayout,
                             QHBoxLayout)

from core.semantic_colors import semantic

from . import energy
from .overview_health import History
from .overview_widgets import Sparkline
from .tab_base import DashModule, DashTab, numeric_item

logger = logging.getLogger(__name__)


class EnergyTab(DashTab):
    REFRESH_MS = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._meter: Optional[energy.EnergyMeter] = None
        self._unavailable: Optional[str] = None
        self._top_source = None
        self._history = History(120)
        self._last_package: Optional[float] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._headline = QLabel("—")
        font = self._headline.font()
        font.setPointSize(font.pointSize() + 12)
        font.setBold(True)
        self._headline.setFont(font)
        layout.addWidget(self._headline)
        self._detail = QLabel("", self)
        self._detail.setStyleSheet("color: gray;")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)
        self._spark = Sparkline(ceiling=1.0)
        self._spark.setMinimumHeight(70)
        layout.addWidget(self._spark)

        split = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self._heading("Cores (measured)"))
        self._cores = QTableWidget(0, 2, self)
        self._cores.setHorizontalHeaderLabels(["Core", "Power"])
        self._cores.verticalHeader().setVisible(False)
        self._cores.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cores.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        left.addWidget(self._cores, 1)
        right = QVBoxLayout()
        right.addWidget(self._heading("Processes (ESTIMATED share of package power)"))
        self._procs = QTableWidget(0, 4, self)
        self._procs.setHorizontalHeaderLabels(["Process", "PID", "CPU", "Est. power"])
        self._procs.verticalHeader().setVisible(False)
        self._procs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._procs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        right.addWidget(self._procs, 1)
        split.addLayout(left, 2)
        split.addLayout(right, 3)
        layout.addLayout(split, 1)
        self._note = QLabel(
            "Power is measured by the CPU's own energy meter (RAPL) and reported by Windows. "
            "Windows has no per-process power reading: the process figures apply each "
            "process's share of CPU time to the package power, so they show who is "
            "responsible, not what an instrument measured. Idle draw is not attributed.", self)
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: gray;")
        layout.addWidget(self._note)

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet("font-weight: bold;")
        return label

    # ---- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        if self._meter is None and self._unavailable is None:
            try:
                self._meter = energy.EnergyMeter()
            except OSError as e:
                self._unavailable = str(e)
                logger.info("energy meter unavailable: %s", e)
        super().start()

    def stop(self) -> None:
        super().stop()
        if self._meter is not None:
            self._meter.close()
            self._meter = None

    # ---- reading ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._unavailable:
            self._headline.setText("No energy meter")
            self._detail.setText(
                f"{self._unavailable.capitalize()}. Windows publishes energy data only when "
                "the CPU exposes RAPL (Intel, and recent AMD); virtual machines and some "
                "laptops do not. Nothing is estimated in its place.")
            return
        if self._meter is None:
            return
        reading = self._meter.read()
        if reading is None:
            self._headline.setText("Measuring…")
            return
        self._show(reading)
        if not self._busy:
            self._busy = True
            self.run(lambda _w: self._process_rows(), self._show_processes, self._processes_failed)

    def _process_rows(self):
        if self._top_source is None:
            from core.procengine.snapshot import SnapshotSource
            self._top_source = SnapshotSource()
        snap = self._top_source.read()
        return [(i.name, i.pid, i.rates.cpu_percent or 0.0) for i in snap.by_pid.values() if i.pid != 0]

    def _show(self, reading: energy.EnergyReading) -> None:
        watts = reading.package_w if reading.package_w is not None else reading.cores_total_w
        self._last_package = watts
        self._history.add(watts)
        self._headline.setText(f"{watts:,.1f} W  package")
        self._detail.setText(
            f"Cores {reading.cores_total_w:,.1f} W   ·   everything else on the package "
            f"{max(0.0, watts - reading.cores_total_w):,.1f} W (cache, memory controller, I/O)   ·   "
            f"{self._meter.watt_hours:,.3f} Wh since this tab opened")
        self._spark._ceiling = max(self._history.values() + [1.0])
        self._spark.set_values(self._history.values(), QColor(semantic("warning")))
        table = self._cores
        cores = sorted(reading.cores_w.items())
        table.setRowCount(len(cores))
        peak = max((w for _, w in cores), default=1.0) or 1.0
        for r, (core, w) in enumerate(cores):
            table.setItem(r, 0, numeric_item(core, f"Core {core}"))
            table.setItem(r, 1, numeric_item(w, f"{w:,.2f} W"))
            if w >= peak * 0.8 and w > 1.0:
                table.item(r, 1).setForeground(QColor(semantic("warning")))

    def _show_processes(self, rows) -> None:
        self._busy = False
        if self._last_package is None:
            return
        est = energy.estimate_process_watts(rows, self._last_package)[:12]
        table = self._procs
        table.setRowCount(len(est))
        for r, (name, pid, cpu, watts) in enumerate(est):
            table.setItem(r, 0, numeric_item(name.lower(), name))
            table.setItem(r, 1, numeric_item(pid, str(pid)))
            table.setItem(r, 2, numeric_item(cpu, f"{cpu:.1f}%"))
            table.setItem(r, 3, numeric_item(watts, f"~{watts:,.1f} W"))
            for c in (2, 3):
                table.item(r, c).setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))

    def _processes_failed(self, message) -> None:
        self._busy = False
        logger.warning("process list for energy estimate failed: %s", message)


class EnergyModule(DashModule):
    name = "Energy"
    icon = "🔋"
    description = "CPU package and per-core power, and who is drawing it"
    tab_class = EnergyTab
