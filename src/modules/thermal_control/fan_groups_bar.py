"""CPU / GPU / Case: the fans by what they cool, each with an Identify button.

Identify holds every fan in the group at 100% for 20 s (see
`view.IDENTIFY_SECONDS` for why 20) and the button counts down; clicking it
again stops them early. Header membership comes from the header's name
(CPU fans and the pump are CPU, chassis fans Case) unless the person moved it
in the Fan curves tab. GPU fans are the GPU's own, through ADLX.
"""
from typing import Dict, List

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.table_ui import set_role

from .engine import view
from .engine.model import FAN, Sensor
from .thermal_service import GPU_PREFIX_ID, ThermalService


class FanGroupsBar(QWidget):
    def __init__(self, service: ThermalService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._sensors: List[Sensor] = []
        self._cards: Dict[str, tuple] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for key, label in view.FAN_GROUPS:
            card = QFrame(self)
            card.setFrameShape(QFrame.Shape.StyledPanel)
            col = QVBoxLayout(card)
            title = QLabel(label, card)
            set_role(title, "heading")
            fans = QLabel("", card)
            fans.setWordWrap(True)
            note = QLabel("", card)
            note.setWordWrap(True)
            set_role(note, "statusWarning")
            button = QPushButton(f"Identify ({view.IDENTIFY_SECONDS} s at 100%)", card)
            button.clicked.connect(lambda _=False, k=key: self._clicked(k))
            for w in (title, fans, note):
                col.addWidget(w)
            col.addStretch(1)
            col.addWidget(button)
            self._cards[key] = (fans, note, button)
            row.addWidget(card, 1)
        self.status = QLabel("", self)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._countdown)
        service.identify_changed.connect(self._countdown)
        service.gpu_fans_changed.connect(lambda _s: self.refresh(self._sensors))

    # ---- what each card shows --------------------------------------------------------

    def refresh(self, sensors: List[Sensor]) -> None:
        self._sensors = sensors
        overrides = self._service.group_overrides()
        for key, (fans, note, button) in self._cards.items():
            if key == "gpu":
                lines, why = self._gpu_lines()
                fans.setText("\n".join(lines) or "No GPU fan control found.")
                note.setText(why)
                button.setEnabled(any(st.controllable for st in self._service.gpu_fans))
                continue
            members = view.group_headers(sensors, overrides, key)
            if not self._service.full:
                fans.setText("Fan headers need administrator rights.")
                note.setText(self._service.reason or "")
                button.setEnabled(False)
                continue
            fans.setText("\n".join(view.group_line(c, sensors) for c in members)
                         or "No header is in this group. Pick one in Fan curves and set its group.")
            note.setText(view.group_note(key, members, sensors))
            button.setEnabled(bool(members))
        self._countdown()

    def _gpu_lines(self):
        lines, reasons = [], []
        statuses = self._service.gpu_fans
        if any(st.controllable for st in statuses):
            statuses = [st for st in statuses if st.controllable]   # the iGPU has no fan to list
        for st in statuses:
            fan = next((s for s in self._sensors if s.kind == FAN and s.source == "d3dkmt"
                        and st.name.lower() in s.hardware.lower()), None)
            rpm = fan.display() if fan is not None and fan.value else "fan stopped (Zero RPM)"
            lines.append(f"{st.name}: {rpm}")
            if not st.controllable:
                reasons.append(st.reason)
        return lines, " ".join(reasons)

    # ---- identify ---------------------------------------------------------------------

    def _targets(self, key: str) -> List[str]:
        running = self._service.identifying()
        if key == "gpu":
            return [t for t in running if t.startswith(GPU_PREFIX_ID)]
        ids = {c.id for c in view.group_headers(self._sensors, self._service.group_overrides(), key)}
        return [t for t in running if t in ids]

    def _clicked(self, key: str) -> None:
        if self._targets(key):
            self._service.stop_identify()
            self.status.setText("Identify stopped; fans are going back to normal.")
            return
        started, problems = self._service.identify_group(key)
        label = dict(view.FAN_GROUPS)[key]
        if started:
            self.status.setText(f"{label}: {', '.join(started)} at 100% for {view.IDENTIFY_SECONDS} s. "
                                "Fans take about 5 s to reach full speed -- listen for the ones that rise.")
        if problems:
            self.status.setText((self.status.text() + "  " if started else "") +
                                f"Could not spin up: {'; '.join(problems)}")

    def _countdown(self) -> None:
        running = self._service.identifying()
        for key, (_f, _n, button) in self._cards.items():
            left = [running[t] for t in self._targets(key)]
            button.setText(f"Stop ({max(left)} s)" if left
                           else f"Identify ({view.IDENTIFY_SECONDS} s at 100%)")
        if running and not self._tick.isActive():
            self._tick.start()
        elif not running:
            self._tick.stop()
