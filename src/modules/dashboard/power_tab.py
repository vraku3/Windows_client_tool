"""Power & Freq: what every logical processor is really running at, which kind
of core it is, and the power plan that steers it."""
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QPushButton, QTableWidget, QVBoxLayout)

from core.confirm import confirm_destructive
from core.semantic_colors import semantic

from . import power, topology
from .overview_health import History
from .overview_widgets import Sparkline
from .tab_base import DashModule, DashTab, numeric_item

logger = logging.getLogger(__name__)

HEADERS = ("CPU", "Core", "Type", "NUMA", "Nominal", "Effective", "Windows limit")


class PowerTab(DashTab):
    REFRESH_MS = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._topo: Optional[topology.Topology] = None
        self._eff: Optional[power.EffectiveFreq] = None
        self._plans: List[power.PowerPlan] = []
        self._history = History(90)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._source_lbl = QLabel("—")
        self._topo_lbl = QLabel("—")
        self._freq_lbl = QLabel("—")
        for lbl in (self._source_lbl, self._topo_lbl, self._freq_lbl):
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        plan_row = QHBoxLayout()
        plan_row.addWidget(QLabel("Power plan:"))
        self._plan_box = QComboBox(self)
        self._plan_box.setMinimumWidth(220)
        plan_row.addWidget(self._plan_box)
        self._apply_btn = QPushButton("Switch plan", self)
        self._apply_btn.clicked.connect(self._switch_plan)
        plan_row.addWidget(self._apply_btn)
        plan_row.addStretch(1)
        layout.addLayout(plan_row)

        self._spark = Sparkline(ceiling=1.0)
        self._spark.setMinimumHeight(56)
        layout.addWidget(self._spark)

        self._table = QTableWidget(0, len(HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        self._note = QLabel(
            "Effective = the nominal clock times the core's live '% Processor "
            "Performance' counter, so boost shows above nominal. Nominal is what "
            "Windows reports as the base clock and does not move when the CPU boosts.",
            self)
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: gray;")
        layout.addWidget(self._note)

    # ---- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        self._open_counter()
        self._load_plans()
        super().start()

    def stop(self) -> None:
        super().stop()
        if self._eff is not None:
            self._eff.close()
            self._eff = None

    def _open_counter(self) -> None:
        if self._eff is not None:
            return
        import psutil
        try:
            self._eff = power.EffectiveFreq(psutil.cpu_count(logical=True) or 0)
        except OSError as e:
            logger.warning("effective-frequency counter unavailable: %s", e)
            self._eff = None

    # ---- reading ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._topo is None:
            self._topo = topology.read_topology()
        nominal = power.read_frequencies()
        if nominal is None:
            self._freq_lbl.setText("Windows would not report processor frequencies.")
            return
        effective = self._eff.read([f.max_mhz for f in nominal]) if self._eff else None
        self._show(nominal, effective)
        self._show_source()

    def _show_source(self) -> None:
        st = power.read_power_status()
        if st.on_battery is None:
            text = "Power source: unknown (" + st.plugged_note + ")"
        elif st.percent is None:
            text = "Power source: mains (" + st.plugged_note + ")"
        else:
            left = f", about {st.minutes_left // 60}h {st.minutes_left % 60:02d}m left" if st.minutes_left else ""
            text = f"Power source: {'battery' if st.on_battery else 'mains'}, {st.percent}%{left}"
        self._source_lbl.setText(text)

    def _show(self, nominal, effective) -> None:
        topo = self._topo
        self._topo_lbl.setText("Topology: " + (topo.summary() if topo else "could not be read"))
        table = self._table
        table.setRowCount(len(nominal))
        core_of = {}
        if topo:
            for core in topo.cores:
                for t in core.threads:
                    core_of[t] = core
        for r, f in enumerate(nominal):
            core = core_of.get(f.index)
            kind = topo.kind_of(f.index) if topo else ""
            eff = effective[r] if effective and r < len(effective) else None
            cells = (numeric_item(f.index, f"CPU {f.index}"),
                     numeric_item(core.index if core else -1, str(core.index) if core else "?"),
                     numeric_item(kind, {"P": "Performance", "E": "Efficiency"}.get(kind, "Standard")),
                     numeric_item(core.numa_node if core else -1, str(core.numa_node) if core else "?"),
                     numeric_item(f.max_mhz, f"{f.max_mhz:,} MHz"),
                     numeric_item(eff or 0, f"{eff:,} MHz" if eff is not None else "measuring…"),
                     numeric_item(f.limit_mhz, ("LIMITED to " if f.throttled else "") + f"{f.limit_mhz:,} MHz"))
            for c, item in enumerate(cells):
                table.setItem(r, c, item)
            if f.throttled:
                table.item(r, 6).setForeground(QColor(semantic("warning")))
            if eff is not None and f.max_mhz and eff > f.max_mhz * 1.02:
                table.item(r, 5).setForeground(QColor(semantic("success")))
        if effective:
            avg = sum(effective) / len(effective)
            self._history.add(avg)
            top = max(max(effective), max(f.max_mhz for f in nominal))
            self._spark.set_values(self._history.values(), QColor(semantic("info")))
            self._spark._ceiling = top
            self._freq_lbl.setText(
                f"Effective clock: average {avg:,.0f} MHz, fastest core {max(effective):,} MHz   ·   "
                + power.summarise(nominal))
        else:
            self._freq_lbl.setText("Measuring effective clocks…   ·   " + power.summarise(nominal))

    # ---- power plan ---------------------------------------------------------------

    def _load_plans(self) -> None:
        self.run(lambda _w: power.list_plans(), self._plans_loaded)

    def _plans_loaded(self, plans) -> None:
        if plans is None:
            self._plan_box.clear()
            self._plan_box.addItem("(could not read power plans)")
            self._apply_btn.setEnabled(False)
            return
        self._plans = plans
        self._plan_box.blockSignals(True)
        self._plan_box.clear()
        for p in plans:
            self._plan_box.addItem(p.name + ("   (active)" if p.active else ""), p.guid)
            if p.active:
                self._plan_box.setCurrentIndex(self._plan_box.count() - 1)
        self._plan_box.blockSignals(False)
        self._apply_btn.setEnabled(bool(plans))

    def _switch_plan(self) -> None:
        guid = self._plan_box.currentData()
        if not guid:
            return
        name = self._plan_box.currentText().replace("   (active)", "")
        if not confirm_destructive(self, "Switch power plan",
                                   f"Switch the active power plan to '{name}'?",
                                   irreversible=False):
            return
        self.run(lambda _w: power.set_active_plan(guid), self._plan_switched)

    def _plan_switched(self, result) -> None:
        ok, message = result
        self._note.setText(message if ok else f"Could not switch: {message}")
        self._load_plans()


class PowerModule(DashModule):
    name = "Power & Freq"
    icon = "⚡"
    description = "Per-core clocks, P/E cores, NUMA and the power plan"
    tab_class = PowerTab
