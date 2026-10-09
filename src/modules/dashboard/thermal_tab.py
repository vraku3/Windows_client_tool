"""Thermals: the machine's real temperatures and fans, plus the throttle and
clock-pressure signals.

Temperatures come from Thermal Control's running service (`read_now`), so
this tab never opens the hardware a second time: CPU Tctl/CCDs, VRM, board,
fans and pump when the app runs elevated (PawnIO + LibreHardwareMonitor),
and the GPU and its fan always (D3DKMT, no admin). It used to say this
machine had no temperatures at all -- true of ACPI thermal zones, which the
X870E Taichi does not publish, but not of the machine."""
import logging
from typing import Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QLabel, QTableWidget, QVBoxLayout

from core.table_ui import set_role
from modules.thermal_control.engine.model import TEMPERATURE
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
        set_role(self._explain, "muted")
        layout.addWidget(self._explain)
        self._readings = QTableWidget(0, 3, self)
        self._readings.setHorizontalHeaderLabels(["Reading", "Hardware", "Now"])
        self._readings.verticalHeader().setVisible(False)
        self._readings.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._readings.setMinimumHeight(200)
        layout.addWidget(self._readings)
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
        set_role(self._spark_note, "muted")
        layout.addWidget(self._spark_note)
        layout.addStretch(1)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        set_role(label, "sectionTitle")
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
        if not self._busy:
            self._busy = True
            self.run(lambda _w: _read_key(), self._show_readings, self._readings_failed)
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

    def _show_readings(self, result) -> None:
        self._busy = False
        readings, reason, found = result
        table = self._readings
        table.setRowCount(len(readings))
        alerts = {label: sev for sev, label, _v, _l in found}
        for r, (label, sensor) in enumerate(readings):
            table.setItem(r, 0, numeric_item(label.lower(), label))
            table.setItem(r, 1, numeric_item(sensor.hardware.lower(), sensor.hardware))
            table.setItem(r, 2, numeric_item(sensor.value if sensor.value is not None else -1, sensor.display()))
            if label in alerts:
                table.item(r, 2).setForeground(QColor(semantic("error" if alerts[label] == "critical"
                                                               else "warning")))
        temps = [(label, s) for label, s in readings if s.kind == TEMPERATURE and s.value is not None]
        if temps:
            label, hottest = max(temps, key=lambda ls: ls[1].value)
            self._headline.setText(f"{hottest.value:.0f} °C  {label}")
        self._explain.setText(reason or "Live readings from Thermal Control. Fan curves are set there.")

    def _readings_failed(self, message) -> None:
        self._busy = False
        self._explain.setText(f"Temperatures could not be read: {message}")

    def _show_zones(self) -> None:
        if self._unavailable:
            self._zones.hide()          # no ACPI zones on this board; the readings above are real
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


def _read_key():
    """(key readings, why some are missing). Runs on a worker."""
    from modules.thermal_control.engine import gpu_kmt, lhm_bridge, view
    from modules.thermal_control.thermal_service import ThermalService
    service = ThermalService.instance
    sensors = service.read_now() if service is not None else gpu_kmt.read_gpus()
    reason = lhm_bridge.unavailable_reason()
    note = (f"GPU only: {reason}." if reason else "")
    # Alerts from the FULL list: a drive's own warn/crit limits are sensors of
    # their own and are not among the key readings.
    return view.key_readings(sensors), note, view.thermal_alerts(sensors)


class ThermalModule(DashModule):
    name = "Thermals"
    icon = "🌡"
    description = "Real temperatures and fans (CPU, GPU, VRM, drives); throttle signals always"
    tab_class = ThermalTab
