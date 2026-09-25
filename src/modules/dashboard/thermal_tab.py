"""Thermals: real temperatures if the firmware publishes any; otherwise a plain
statement of that, plus the real throttle and clock-pressure signals."""
import logging
from typing import Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QLabel, QTableWidget, QVBoxLayout

from core.semantic_colors import semantic

from . import power, thermal
from .overview_health import History
from .overview_widgets import Sparkline
from .tab_base import DashModule, DashTab, numeric_item

logger = logging.getLogger(__name__)

HOT_C = 85.0


class ThermalTab(DashTab):
    REFRESH_MS = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._reader: Optional[thermal.ThermalReader] = None
        self._unavailable: Optional[str] = None
        self._eff: Optional[power.EffectiveFreq] = None
        self._history = History(120)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._headline = QLabel("—")
        font = self._headline.font()
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        self._headline.setFont(font)
        layout.addWidget(self._headline)
        self._explain = QLabel("", self)
        self._explain.setWordWrap(True)
        self._explain.setStyleSheet("color: gray;")
        layout.addWidget(self._explain)
        self._zones = QTableWidget(0, 2, self)
        self._zones.setHorizontalHeaderLabels(["Sensor", "Temperature"])
        self._zones.verticalHeader().setVisible(False)
        self._zones.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._zones.setMaximumHeight(160)
        layout.addWidget(self._zones)
        layout.addWidget(self._label("Clock pressure (real signals, not temperatures)"))
        self._pressure = QLabel("—", self)
        self._pressure.setWordWrap(True)
        layout.addWidget(self._pressure)
        self._spark = Sparkline(ceiling=1.5)
        self._spark.setMinimumHeight(80)
        layout.addWidget(self._spark)
        self._spark_note = QLabel("Effective clock as a fraction of nominal, last few minutes.", self)
        self._spark_note.setStyleSheet("color: gray;")
        layout.addWidget(self._spark_note)
        layout.addStretch(1)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet("font-weight: bold;")
        return label

    def start(self) -> None:
        if self._reader is None and self._unavailable is None:
            try:
                self._reader = thermal.ThermalReader()
            except OSError as e:
                self._unavailable = str(e)
        if self._eff is None:
            import psutil
            try:
                self._eff = power.EffectiveFreq(psutil.cpu_count(logical=True) or 0)
            except OSError as e:
                logger.warning("clock counter unavailable: %s", e)
        super().start()

    def stop(self) -> None:
        super().stop()
        for closable in (self._reader, self._eff):
            if closable is not None:
                closable.close()
        self._reader = self._eff = None

    def refresh(self) -> None:
        self._show_zones()
        freqs = power.read_frequencies()
        if freqs is None:
            self._pressure.setText("Windows would not report processor frequencies.")
            return
        effective = self._eff.read([f.max_mhz for f in freqs]) if self._eff else None
        state = thermal.pressure(freqs, effective)
        self._pressure.setText(thermal.describe_pressure(state))
        if state["clock_ratio"] is not None:
            self._history.add(state["clock_ratio"])
            colour = semantic("warning") if state["limited_cores"] else semantic("info")
            self._spark.set_values(self._history.values(), QColor(colour))

    def _show_zones(self) -> None:
        if self._unavailable:
            self._headline.setText("No temperature sensors published")
            self._explain.setText(
                "This machine's firmware does not publish ACPI thermal zones to Windows "
                "(common on desktop boards), so Windows itself has no temperature to give. "
                "Reading the CPU die temperature would need a vendor driver, which this app "
                "does not install. No value is shown rather than a guess; the throttle and "
                "clock signals below are real.")
            self._zones.hide()          # an empty sensor table would only look broken
            return
        zones = self._reader.read() if self._reader else None
        if zones is None:
            self._headline.setText("Measuring…")
            return
        hottest = zones[0] if zones else None
        self._headline.setText(f"{hottest.celsius:.0f} °C  {hottest.name}" if hottest else "No readings")
        self._explain.setText("ACPI thermal zones as published by the firmware. Zone names are the "
                              "board vendor's; a zone is not necessarily the CPU.")
        table = self._zones
        table.setRowCount(len(zones))
        for r, z in enumerate(zones):
            table.setItem(r, 0, numeric_item(z.name.lower(), z.name))
            table.setItem(r, 1, numeric_item(z.celsius, f"{z.celsius:.1f} °C"))
            if z.celsius >= HOT_C:
                table.item(r, 1).setForeground(QColor(semantic("error")))


class ThermalModule(DashModule):
    name = "Thermals"
    icon = "🌡"
    description = "Temperature sensors where the firmware has them; throttle signals always"
    tab_class = ThermalTab
