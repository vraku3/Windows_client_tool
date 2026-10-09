"""Thermal Control: every temperature and fan, and fan curves on the headers."""
import logging
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import set_role
from ui.error_banner import ErrorBanner

from .curve_editor import CurveEditor
from .engine import curves as cv
from .engine import view
from .engine.model import CONTROL, FAN, TEMPERATURE, Sensor, is_pump
from .thermal_service import ThermalService, marker_path_for

logger = logging.getLogger(__name__)

GPU_PREFIX = "gpu:"

_CONFIRM_GPU = ("This writes a fan curve into the GPU itself. The GPU's firmware runs it, so it keeps "
                "working when this app is closed, and the GPU's own overheat protection stays in "
                "charge. AMD checks the curve before it is accepted.\n\n'Back to factory' returns "
                "the card to the driver's automatic fan control.\n\nWrite it?")

_CONFIRM = ("Fan curves take the selected fan header away from the BIOS and drive it from this "
            "app.\n\nSafety rules always apply: the fan never goes below {floor:.0f}%, goes to 100% "
            "at the critical temperature or if its sensor stops answering, and is handed back to "
            "the BIOS when you disable the curve or close the app. If the app crashes, the fan "
            "keeps its last speed until the app starts again and hands it back.\n\nEnable it?")


class ThermalWidget(QWidget):
    def __init__(self, service: ThermalService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._sensors: List[Sensor] = []
        self._range: Dict[str, Tuple[float, float]] = {}
        self._selected: Optional[str] = None
        self._confirmed = False
        self._gpu_confirmed = False
        self._build()
        service.updated.connect(self._on_sensors)
        service.state_changed.connect(self._status.setText)
        service.gpu_fans_changed.connect(self._on_gpu_fans)
        self._on_gpu_fans(service.gpu_fans)

    # ---- layout ---------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self._status = QLabel(self._initial_status(), self)
        self._status.setWordWrap(True)
        set_role(self._status, "muted")
        self._banner = ErrorBanner(parent=self)
        layout.addWidget(self._status)
        layout.addWidget(self._banner)
        tabs = QTabWidget(self)
        tabs.addTab(self._build_sensors(), "Sensors")
        tabs.addTab(self._build_curves(), "Fan curves")
        layout.addWidget(tabs, 1)

    def _initial_status(self) -> str:
        if self._service.reason:
            return f"Showing GPU temperatures only: {self._service.reason}."
        return "Opening the hardware…"

    def _build_sensors(self) -> QWidget:
        tree = QTreeWidget(self)
        tree.setHeaderLabels(["Sensor", "Now", "Min", "Max", "Limits"])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.setRootIsDecorated(True)
        self._tree = tree
        return tree

    def _build_curves(self) -> QWidget:
        page = QSplitter(Qt.Orientation.Horizontal, self)
        left = QWidget(page)
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        self._headers = QListWidget(left)
        self._headers.currentItemChanged.connect(self._header_picked)
        left_col.addWidget(self._headers, 1)
        self._gpu_note = QLabel("Checking GPU fans…", left)
        self._gpu_note.setWordWrap(True)
        set_role(self._gpu_note, "muted")
        left_col.addWidget(self._gpu_note)
        right = QWidget(page)
        col = QVBoxLayout(right)
        self._pump_note = QLabel("", right)
        self._pump_note.setWordWrap(True)
        set_role(self._pump_note, "statusWarning")
        col.addWidget(self._pump_note)
        self._editor = CurveEditor(right)
        col.addWidget(self._editor, 1)
        col.addLayout(self._build_form(right))
        col.addLayout(self._build_buttons(right))
        page.setStretchFactor(1, 1)
        page.setSizes([420, 800])
        self._right = right
        right.setEnabled(False)
        self._form.setRowVisible(self._zero, False)
        self._pump_note.setText("Pick a fan on the left." if self._service.full or not self._service.reason
                                else f"Fan headers need administrator rights ({self._service.reason}); "
                                     "GPU fans, listed when found, do not.")
        return page

    def _build_form(self, parent) -> QFormLayout:
        form = QFormLayout()
        self._source = QComboBox(parent)
        self._critical = QDoubleSpinBox(parent)
        self._critical.setRange(50, 110)
        self._critical.setDecimals(0)
        self._critical.setSuffix(" °C")
        self._critical.valueChanged.connect(self._redraw_editor)
        self._hyst = QDoubleSpinBox(parent)
        self._hyst.setRange(1, 10)
        self._hyst.setDecimals(0)
        self._hyst.setSuffix(" °C")
        self._enabled = QCheckBox("Drive this header with the curve", parent)
        self._zero = QCheckBox("Zero RPM: let the fan stop when the GPU is cool", parent)
        self._zero.clicked.connect(self._zero_clicked)
        form.addRow("Follow temperature:", self._source)
        form.addRow("Full speed at or above:", self._critical)
        form.addRow("Hysteresis:", self._hyst)
        form.addRow("", self._enabled)
        form.addRow("", self._zero)
        self._form = form
        return form

    def _build_buttons(self, parent) -> QHBoxLayout:
        row = QHBoxLayout()
        apply_btn = QPushButton("Apply", parent)
        apply_btn.clicked.connect(self._apply)
        default_btn = QPushButton("Default curve", parent)
        default_btn.clicked.connect(self._reset_points)
        bios_btn = QPushButton("Hand back to BIOS", parent)
        bios_btn.clicked.connect(self._hand_back)
        self._bios_btn = bios_btn
        for b in (apply_btn, default_btn, bios_btn):
            row.addWidget(b)
        row.addStretch(1)
        return row

    def _on_gpu_fans(self, statuses) -> None:
        if not statuses:
            return
        lines = [f"{s.name}: {s.reason}" for s in statuses]
        self._gpu_note.setText("GPU fans\n" + "\n".join(lines))
        self._fill_headers()
        if self._selected and self._selected.startswith(GPU_PREFIX):
            self._header_picked(self._headers.currentItem())

    # ---- live data ---------------------------------------------------------------------

    def _on_sensors(self, sensors: List[Sensor]) -> None:
        self._sensors = sensors
        for s in sensors:
            if s.value is not None:
                lo, hi = self._range.get(s.id, (s.value, s.value))
                self._range[s.id] = (min(lo, s.value), max(hi, s.value))
        self._fill_tree()
        self._fill_headers()
        self._refresh_live()
        errors = self._service.errors
        if errors:
            self._banner.set_error("; ".join(errors[:3]))
        else:
            self._banner.clear()

    def _fill_tree(self) -> None:
        limits = view.limits_by_hardware(self._sensors)
        expanded = {self._tree.topLevelItem(i).text(0): self._tree.topLevelItem(i).isExpanded()
                    for i in range(self._tree.topLevelItemCount())}
        self._tree.clear()
        for hw, rows in view.groups(self._sensors):
            lim = limits.get(hw, {})
            text = ", ".join(f"{k} {v:.0f} °C" for k, v in lim.items())
            top = QTreeWidgetItem([hw, "", "", "", text])
            for s in rows:
                lo, hi = self._range.get(s.id, (None, None))
                fmt = (lambda v: "" if v is None else (f"{v:,.0f}" if s.kind != TEMPERATURE else f"{v:.1f}"))
                label = f"{s.name} (duty)" if s.kind == CONTROL else s.name
                item = QTreeWidgetItem(top, [label, s.display(), fmt(lo), fmt(hi), ""])
                self._colour(item, s, lim)
            self._tree.addTopLevelItem(top)
            top.setExpanded(expanded.get(hw, True))

    @staticmethod
    def _colour(item: QTreeWidgetItem, s: Sensor, lim: Dict[str, float]) -> None:
        from PyQt6.QtGui import QColor
        if s.kind != TEMPERATURE or s.value is None:
            return
        if "crit" in lim and s.value >= lim["crit"]:
            item.setForeground(1, QColor(semantic("error")))
        elif "warn" in lim and s.value >= lim["warn"]:
            item.setForeground(1, QColor(semantic("warning")))

    def _fill_headers(self) -> None:
        curves = self._service.curves()
        controls = [s for s in self._sensors if s.kind == CONTROL and s.controllable]
        self._headers.blockSignals(True)
        current = self._selected
        self._headers.clear()
        for c in controls:
            curve = curves.for_control(c.id)
            text = view.header_line(c, self._sensors, bool(curve and curve.enabled))
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            self._headers.addItem(item)
            if c.id == current:
                self._headers.setCurrentItem(item)
        for st in self._service.gpu_fans:
            if not st.controllable:
                continue
            gid = GPU_PREFIX + st.name
            fan = next((s for s in self._sensors if s.kind == FAN and s.hardware == st.name), None)
            rpm = f" · {fan.display()}" if fan else ""
            item = QListWidgetItem(f"GPU · {st.name}  —  {st.reason.replace('fan curve available ', '')}{rpm}")
            item.setToolTip(item.text())
            item.setData(Qt.ItemDataRole.UserRole, gid)
            self._headers.addItem(item)
            if gid == current:
                self._headers.setCurrentItem(item)
        self._headers.blockSignals(False)
        if current is None and self._headers.count():
            self._headers.setCurrentRow(0)
        self._fill_sources()

    def _fill_sources(self) -> None:
        keep = self._source.currentData()
        self._source.blockSignals(True)
        self._source.clear()
        for s in view.temperature_sources(self._sensors):
            self._source.addItem(f"{s.hardware} · {s.name}  ({s.display()})", s.id)
        if keep:
            i = self._source.findData(keep)
            if i >= 0:
                self._source.setCurrentIndex(i)
        self._source.blockSignals(False)

    def _refresh_live(self) -> None:
        if self._selected is None:
            return
        if self._selected.startswith(GPU_PREFIX):
            name = self._selected[len(GPU_PREFIX):]
            temp = next((s.value for s in self._sensors
                         if s.kind == TEMPERATURE and s.hardware == name), None)
            self._editor.set_live(temp, None)
            return
        temps = {s.id: s.value for s in self._sensors if s.kind == TEMPERATURE}
        duty = next((s.value for s in self._sensors if s.id == self._selected), None)
        self._editor.set_live(temps.get(self._source.currentData()), duty)

    # ---- editing ---------------------------------------------------------------------

    def _header_picked(self, item, _previous=None) -> None:
        if item is None:
            return
        self._selected = item.data(Qt.ItemDataRole.UserRole)
        self._right.setEnabled(True)
        is_gpu = self._selected.startswith(GPU_PREFIX)
        for w in (self._source, self._critical, self._hyst, self._enabled):
            self._form.setRowVisible(w, not is_gpu)      # header-only settings
        self._form.setRowVisible(self._zero, is_gpu)
        self._bios_btn.setText("Back to factory" if is_gpu else "Hand back to BIOS")
        if is_gpu:
            self._gpu_picked(self._selected[len(GPU_PREFIX):])
            return
        name = next((s.name for s in self._sensors if s.id == self._selected), "")
        pump = is_pump(name)
        curve = self._service.curves().for_control(self._selected) or cv.FanCurve(
            self._selected, self._default_source(), cv.default_points(pump), pump=pump, label=name)
        self._pump_note.setText(
            "This is a PUMP header. Pumps are held at 70% or more; slowing a pump can stall it "
            "or overheat it." if pump else "")
        i = self._source.findData(curve.source_id)
        if i >= 0:
            self._source.setCurrentIndex(i)
        self._critical.setValue(curve.critical_c)
        self._hyst.setValue(curve.hysteresis_c)
        self._enabled.setChecked(curve.enabled)
        self._editor.set_curve(curve.points, curve.floor, curve.critical_c)
        self._refresh_live()

    def _gpu_picked(self, name: str) -> None:
        st = self._service.gpu_status(name)
        if st is None or st.gpu is None:
            return
        lo = st.gpu.speed_range[0] if st.gpu.speed_range else 0
        self._pump_note.setText(f"{name}: {len(st.gpu.curve)} points, fan {lo}-100%, run by the GPU's "
                                "own firmware from its own temperature sensor.")
        self._editor.set_curve(st.gpu.curve, lo, None, fixed=True)
        self._zero.setEnabled(st.gpu.zero_rpm is not None)
        self._zero.setChecked(bool(st.gpu.zero_rpm))
        self._refresh_live()

    def _default_source(self) -> str:
        sources = view.temperature_sources(self._sensors)
        return sources[0].id if sources else ""

    def _redraw_editor(self) -> None:
        if self._selected is not None:
            name = next((s.name for s in self._sensors if s.id == self._selected), "")
            floor = cv.MIN_PUMP_PERCENT if is_pump(name) else cv.MIN_FAN_PERCENT
            self._editor.set_curve(self._editor.points(), floor, self._critical.value())

    def _reset_points(self) -> None:
        if self._selected is None:
            return
        if self._selected.startswith(GPU_PREFIX):
            self._gpu_picked(self._selected[len(GPU_PREFIX):])     # back to what the GPU holds
            return
        name = next((s.name for s in self._sensors if s.id == self._selected), "")
        pump = is_pump(name)
        self._editor.set_curve(cv.default_points(pump),
                               cv.MIN_PUMP_PERCENT if pump else cv.MIN_FAN_PERCENT, self._critical.value())

    def _current_curve(self) -> Optional[cv.FanCurve]:
        if self._selected is None:
            return None
        name = next((s.name for s in self._sensors if s.id == self._selected), "")
        return cv.FanCurve(self._selected, self._source.currentData() or "", self._editor.points(),
                           enabled=self._enabled.isChecked(), pump=is_pump(name),
                           critical_c=self._critical.value(), hysteresis_c=self._hyst.value(), label=name)

    def _apply(self) -> None:
        if self._selected and self._selected.startswith(GPU_PREFIX):
            self._apply_gpu(self._selected[len(GPU_PREFIX):])
            return
        curve = self._current_curve()
        if curve is None:
            return
        if curve.enabled and not self._confirmed:
            answer = QMessageBox.question(self, "Take this fan from the BIOS?",
                                          _CONFIRM.format(floor=curve.floor),
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._confirmed = True
        problems = self._service.save_curve(curve)
        if problems:
            self._banner.set_error("Not applied: " + "; ".join(problems))
            return
        self._banner.clear()
        self._status.setText(f"{curve.label}: " + ("following the curve." if curve.enabled
                                                    else "curve saved, header left with the BIOS."))

    def _apply_gpu(self, name: str) -> None:
        if not self._gpu_confirmed:
            answer = QMessageBox.question(self, "Write this curve to the GPU?", _CONFIRM_GPU,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._gpu_confirmed = True
        problems = self._service.apply_gpu_curve(name, self._editor.points())
        if problems:
            self._banner.set_error("Not written: " + "; ".join(problems))
        else:
            self._banner.clear()
            self._status.setText(f"{name}: writing the curve…")

    def _zero_clicked(self, on: bool) -> None:
        if self._selected and self._selected.startswith(GPU_PREFIX):
            self._service.set_gpu_zero_rpm(self._selected[len(GPU_PREFIX):], on)

    def _hand_back(self) -> None:
        if self._selected is None:
            return
        if self._selected.startswith(GPU_PREFIX):
            self._service.gpu_back_to_factory(self._selected[len(GPU_PREFIX):])
            self._status.setText("Returning the GPU to factory fan control…")
            return
        self._service.release(self._selected)
        self._enabled.setChecked(False)
        self._status.setText("Handed back to the BIOS.")


class ThermalControlModule(BaseModule):
    name = "Thermal Control"
    icon = "🌡"
    description = "Every temperature and fan on the machine, and fan curves for the fan headers"
    group = ModuleGroup.SYSTEM
    requires_admin = True
    read_only_unelevated = True

    def __init__(self) -> None:
        super().__init__()
        self.service: Optional[ThermalService] = None
        self._widget: Optional[ThermalWidget] = None

    def on_start(self, app) -> None:
        self.app = app
        self.service = ThermalService(app, marker_path_for(app))
        self.service.start()

    def create_widget(self) -> QWidget:
        self._widget = ThermalWidget(self.service)
        return self._widget

    def on_activate(self) -> None:
        if self.service is not None:
            self.service.add_viewer()

    def on_deactivate(self) -> None:
        if self.service is not None:
            self.service.remove_viewer()

    def on_stop(self) -> None:
        if self.service is not None:
            self.service.shutdown()
        super().on_stop()

    def get_refresh_interval(self) -> Optional[int]:
        return None          # the service drives its own timer
