# src/modules/disk_health/disk_health_module.py
import logging
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QScrollArea, QTableWidget,
    QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import Worker
from ui.empty_state import EmptyState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SmartAttribute:
    id: int
    name: str
    value: int       # normalised 0-255
    worst: int
    threshold: int
    raw: str
    failing: bool    # value <= threshold


@dataclass
class DiskInfo:
    index: int
    model: str
    serial: str
    size_gb: float
    interface: str   # e.g. SATA, NVMe, USB
    status: str      # OK / Pred. Fail / Unknown
    temperature: Optional[int]       # °C, may be None
    power_on_hours: Optional[int]
    reallocated_sectors: Optional[int]
    pending_sectors: Optional[int]
    smart_attrs: List[SmartAttribute] = field(default_factory=list)
    raw_output: str = ""


# ---------------------------------------------------------------------------
# WMI / PowerShell SMART reader
# ---------------------------------------------------------------------------

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$disks = Get-WmiObject -Class Win32_DiskDrive
foreach ($d in $disks) {
    $smart = Get-WmiObject -Namespace root\wmi -Class MSStorageDriver_FailurePredictStatus `
             | Where-Object { $_.InstanceName -like "*$($d.PNPDeviceID.Replace('\','_'))*" }
    $data  = Get-WmiObject -Namespace root\wmi -Class MSStorageDriver_FailurePredictData `
             | Where-Object { $_.InstanceName -like "*$($d.PNPDeviceID.Replace('\','_'))*" }
    $thresh= Get-WmiObject -Namespace root\wmi -Class MSStorageDriver_FailurePredictThresholds `
             | Where-Object { $_.InstanceName -like "*$($d.PNPDeviceID.Replace('\','_'))*" }
    $predFail = if ($smart) { $smart.PredictFailure } else { $false }
    $sizeGB = [math]::Round($d.Size / 1GB, 1)
    Write-Output "DISK_START"
    Write-Output "Index=$($d.Index)"
    Write-Output "Model=$($d.Model)"
    Write-Output "Serial=$($d.SerialNumber)"
    Write-Output "SizeGB=$sizeGB"
    Write-Output "Interface=$($d.InterfaceType)"
    Write-Output "Status=$($d.Status)"
    Write-Output "PredFail=$predFail"
    if ($data -and $thresh) {
        $rawBytes   = $data.VendorSpecific
        $threshBytes= $thresh.VendorSpecific
        $offset = 2
        for ($i = 0; $i -lt 30; $i++) {
            $base = $offset + $i * 12
            if ($base + 12 -gt $rawBytes.Length) { break }
            $attrId = $rawBytes[$base]
            if ($attrId -eq 0) { continue }
            $val    = $rawBytes[$base + 3]
            $worst  = $rawBytes[$base + 4]
            $thr    = $threshBytes[$base + 1]
            $raw5   = [uint64]0
            for ($b = 5; $b -le 10; $b++) { $raw5 = $raw5 + ([uint64]$rawBytes[$base + $b] -shl (($b-5)*8)) }
            Write-Output "ATTR=$attrId,$val,$worst,$thr,$raw5"
        }
    }
    Write-Output "DISK_END"
}
"""

# Well-known SMART attribute names
_ATTR_NAMES = {
    1:   "Read Error Rate",
    3:   "Spin Up Time",
    4:   "Start/Stop Count",
    5:   "Reallocated Sectors",
    7:   "Seek Error Rate",
    9:   "Power-On Hours",
    10:  "Spin Retry Count",
    12:  "Power Cycle Count",
    177: "Wear Leveling Count",
    179: "Used Reserved Block Count",
    181: "Program Fail Count",
    182: "Erase Fail Count",
    183: "Runtime Bad Block",
    187: "Uncorrectable Error Count",
    190: "Airflow Temperature",
    194: "Temperature",
    195: "Hardware ECC Recovered",
    196: "Reallocation Event Count",
    197: "Pending Sector Count",
    198: "Uncorrectable Sector Count",
    199: "UltraDMA CRC Error Count",
    200: "Write Error Rate",
    231: "SSD Life Left",
    232: "Endurance Remaining",
    233: "Media Wearout Indicator",
    240: "Head Flying Hours",
    241: "Total LBAs Written",
    242: "Total LBAs Read",
}


def _query_disks() -> List[DiskInfo]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout
    except Exception as e:
        logger.error("SMART query failed: %s", e)
        return []

    disks: List[DiskInfo] = []
    current: Optional[dict] = None

    for line in output.splitlines():
        line = line.strip()
        if line == "DISK_START":
            current = {"attrs": [], "raw_lines": []}
        elif line == "DISK_END":
            if current is not None:
                disks.append(_build_disk(current))
            current = None
        elif current is not None:
            current["raw_lines"].append(line)
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "ATTR":
                    parts = val.split(",")
                    if len(parts) == 5:
                        try:
                            attr_id = int(parts[0])
                            attr_val = int(parts[1])
                            attr_worst = int(parts[2])
                            attr_thr = int(parts[3])
                            attr_raw = str(int(parts[4]))
                            current["attrs"].append(SmartAttribute(
                                id=attr_id,
                                name=_ATTR_NAMES.get(attr_id, f"Attr {attr_id}"),
                                value=attr_val,
                                worst=attr_worst,
                                threshold=attr_thr,
                                raw=attr_raw,
                                failing=attr_val <= attr_thr and attr_thr > 0,
                            ))
                        except ValueError:
                            logger.debug("Ignored ValueError", exc_info=True)
                else:
                    current[key] = val

    return disks


def _build_disk(d: dict) -> DiskInfo:
    attrs: List[SmartAttribute] = d.get("attrs", [])
    temp = next((a.raw for a in attrs if a.id == 194), None)
    if temp is None:
        temp = next((a.raw for a in attrs if a.id == 190), None)
    poh = next((a.raw for a in attrs if a.id == 9), None)
    reallocated = next((a.raw for a in attrs if a.id == 5), None)
    pending = next((a.raw for a in attrs if a.id == 197), None)

    pred_fail = d.get("PredFail", "False").lower() == "true"
    status = d.get("Status", "Unknown")
    if pred_fail:
        status = "PREDICTED FAILURE"
    elif status.upper() == "OK":
        status = "Healthy"

    return DiskInfo(
        index=_int(d.get("Index", "0")),
        model=d.get("Model", "Unknown").strip(),
        serial=d.get("Serial", "—").strip(),
        size_gb=_float(d.get("SizeGB", "0")),
        interface=d.get("Interface", "Unknown"),
        status=status,
        temperature=_int(temp) if temp is not None else None,
        power_on_hours=_int(poh) if poh is not None else None,
        reallocated_sectors=_int(reallocated) if reallocated is not None else None,
        pending_sectors=_int(pending) if pending is not None else None,
        smart_attrs=attrs,
        raw_output="\n".join(d.get("raw_lines", [])),
    )


def _int(v) -> int:
    try:
        return int(str(v).split(".")[0])
    except (ValueError, TypeError):
        return 0


def _float(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Disk card widget
# ---------------------------------------------------------------------------

class _DiskCard(QFrame):
    def __init__(self, disk: DiskInfo, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._setup(disk)

    def _setup(self, d: DiskInfo) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(12, 10, 12, 10)
        vbox.setSpacing(6)

        # Header row: model + status badge
        header = QHBoxLayout()
        model_lbl = QLabel(f"💽  {d.model}")
        font = model_lbl.font()
        font.setBold(True)
        model_lbl.setFont(font)
        header.addWidget(model_lbl, stretch=1)

        status_lbl = QLabel(d.status)
        if "FAIL" in d.status.upper():
            status_lbl.setStyleSheet(
                "background:#e06c75; color:white; padding:2px 8px; border-radius:3px; font-weight:bold;")
        elif d.status == "Healthy":
            status_lbl.setStyleSheet(
                "background:#4ec9b0; color:#1e1e1e; padding:2px 8px; border-radius:3px; font-weight:bold;")
        else:
            status_lbl.setStyleSheet(
                "background:#e5c07b; color:#1e1e1e; padding:2px 8px; border-radius:3px;")
        header.addWidget(status_lbl)
        vbox.addLayout(header)

        # Details grid
        details = [
            ("Serial",    d.serial),
            ("Size",      f"{d.size_gb:.1f} GB"),
            ("Interface", d.interface),
            ("Temp",      f"{d.temperature}°C" if d.temperature is not None else "—"),
            ("Power-On",  f"{d.power_on_hours:,} hours" if d.power_on_hours is not None else "—"),
            ("Reallocated sectors", str(d.reallocated_sectors) if d.reallocated_sectors is not None else "—"),
            ("Pending sectors",     str(d.pending_sectors) if d.pending_sectors is not None else "—"),
        ]
        grid = QHBoxLayout()
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        for i, (k, v) in enumerate(details):
            lbl = QLabel(f"<b>{k}:</b>  {v}")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            if i % 2 == 0:
                col1.addWidget(lbl)
            else:
                col2.addWidget(lbl)
        col1.addStretch()
        col2.addStretch()
        grid.addLayout(col1)
        grid.addLayout(col2)
        vbox.addLayout(grid)

        # SMART attribute table
        if d.smart_attrs:
            attr_lbl = QLabel("S.M.A.R.T. Attributes")
            attr_lbl.setStyleSheet("color: gray; font-weight: bold; margin-top: 6px;")
            vbox.addWidget(attr_lbl)

            tbl = QTableWidget(len(d.smart_attrs), 5)
            tbl.setHorizontalHeaderLabels(["ID", "Name", "Value", "Worst", "Threshold"])
            center_header(tbl)
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            tbl.verticalHeader().setVisible(False)
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.setAlternatingRowColors(True)
            tbl.setMaximumHeight(200)

            for row, attr in enumerate(d.smart_attrs):
                tbl.setItem(row, 0, centered_item(f"{attr.id:03d}"))
                tbl.setItem(row, 1, centered_item(attr.name))
                tbl.setItem(row, 2, centered_item(str(attr.value)))
                tbl.setItem(row, 3, centered_item(str(attr.worst)))
                tbl.setItem(row, 4, centered_item(str(attr.threshold)))
                if attr.failing:
                    for col in range(5):
                        item = tbl.item(row, col)
                        if item:
                            item.setForeground(
                                tbl.palette().color(tbl.palette().ColorRole.BrightText))
                            item.setBackground(
                                tbl.palette().color(tbl.palette().ColorRole.Mid))

            vbox.addWidget(tbl)


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class _DiskHealthWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers = []
        self._scanning = False
        self._thread_pool = QThreadPool.globalInstance()
        self._setup_ui()

    def _setup_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)

        # Toolbar
        tb = QHBoxLayout()
        self._scan_btn = QPushButton("🔍  Scan Drives")
        self._scan_btn.clicked.connect(self._do_scan)
        tb.addWidget(self._scan_btn)
        self._status_lbl = QLabel("Click Scan to read S.M.A.R.T. data.")
        self._status_lbl.setStyleSheet("color: gray;")
        tb.addWidget(self._status_lbl, stretch=1)
        vbox.addLayout(tb)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.hide()
        vbox.addWidget(self._progress)

        # Warning banner
        warn = QLabel(
            "ℹ️  S.M.A.R.T. data requires administrator rights and may not be available for "
            "all drive types (e.g. some USB enclosures, NVMe via third-party controllers)."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #858585; font-size: 11px;")
        vbox.addWidget(warn)

        # Scroll area for disk cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_widget)
        self._scroll = scroll
        vbox.addWidget(scroll, stretch=1)

        # The pane needs an explicit scan before it can show anything, so it
        # says so in the space it is explaining. It takes the scroll area's
        # place rather than sitting inside it: the cards layout ends in a
        # stretch, which would pin the message to the top of 800px of nothing.
        self._empty = EmptyState(
            "💽", "No drives scanned yet",
            "S.M.A.R.T. data is read on demand. Administrator rights give "
            "readings for drives that will not answer otherwise.",
            action_text="🔍  Scan Drives")
        self._empty.action_triggered.connect(self._do_scan)
        vbox.addWidget(self._empty, stretch=1)
        self._update_empty_state()

    def refresh(self) -> None:
        """Trigger a background rescan. Idempotent — skips if already scanning."""
        if self._scanning:
            return
        self._do_scan()

    def _do_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.setEnabled(False)
        self._status_lbl.setText("Scanning drives…")
        self._progress.show()
        self._clear_cards()

        def work(_w):
            return _query_disks()

        def on_result(disks: List[DiskInfo]):
            self._scanning = False
            self._progress.hide()
            self._scan_btn.setEnabled(True)
            self._clear_cards()
            if not disks:
                self._status_lbl.setText(
                    "No drives found — try running as Administrator.")
                return
            failing = sum(1 for d in disks if "FAIL" in d.status.upper())
            self._status_lbl.setText(
                f"Found {len(disks)} drive(s)"
                + (f" — ⚠️ {failing} predicted failure(s)!" if failing else " — all healthy"))
            for d in disks:
                card = _DiskCard(d)
                self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._update_empty_state()

        def on_error(err: str):
            self._scanning = False
            self._progress.hide()
            self._scan_btn.setEnabled(True)
            self._status_lbl.setText(f"Error: {err}")

        w = Worker(work)
        w.signals.result.connect(on_result)
        w.signals.error.connect(on_error)
        self._workers.append(w)
        self._thread_pool.start(w)

    def _clear_cards(self) -> None:
        # Everything except the empty state, which is a fixture of the pane
        # rather than a result -- the old loop deleted whatever sat at index 0.
        for index in reversed(range(self._cards_layout.count())):
            item = self._cards_layout.itemAt(index)
            widget = item.widget() if item else None
            if widget is not None and widget is not self._empty:
                self._cards_layout.takeAt(index)
                widget.deleteLater()
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        """Show the placeholder exactly when there is no result to show."""
        cards = 0
        for index in range(self._cards_layout.count()):
            item = self._cards_layout.itemAt(index)
            widget = item.widget() if item else None
            if widget is not None and widget is not self._empty:
                cards += 1
        self._empty.setVisible(cards == 0)
        self._scroll.setVisible(cards > 0)

    def cancel_all_workers(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class DiskHealthModule(BaseModule):
    name = "Disk Health"
    icon = "💾"
    description = "S.M.A.R.T. drive health, temperature, reallocated sectors, failure prediction"
    requires_admin = True
    group = ModuleGroup.SYSTEM
    _scanned_once = False

    def create_widget(self) -> QWidget:
        self._widget = _DiskHealthWidget()
        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def on_activate(self) -> None:
        # First open used to show "No drives scanned yet" and wait for a click
        # (or for the 5-minute auto-refresh, whose first tick is a full
        # interval after the tab opens). The tab exists to show drive health,
        # so it scans the first time it is opened, and only then.
        widget = getattr(self, "_widget", None)
        if widget is None or self._scanned_once:
            return
        self._scanned_once = True
        widget._do_scan()

    def on_deactivate(self) -> None:
        if hasattr(self, "_widget"):
            self._widget.cancel_all_workers()

    def refresh_data(self) -> None:
        if hasattr(self, "_widget"):
            self._widget.refresh()

    def get_refresh_interval(self) -> Optional[int]:
        """Auto-refresh SMART data every 5 minutes."""
        return 300_000

    def get_status_info(self) -> str:
        return "Disk Health"
