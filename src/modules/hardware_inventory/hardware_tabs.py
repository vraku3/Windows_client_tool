"""Tab bodies for the Hardware Info pane (Qt side).

Each ``setup_*`` receives the tab's content layout and the loader's result.
The loaders run on a COMWorker; the setups run on the UI thread.
"""
import logging
from typing import List

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QTableWidget, QVBoxLayout, QWidget,
)

from core.table_ui import centered_item, fit_last, fit_table, set_role
from modules.hardware_inventory import asset_parse as ap
from modules.hardware_inventory import asset_reader as ar
from modules.hardware_inventory import hardware_reader as hr
from ui.table_items import fit_table_height, numeric_item, paint_severity

logger = logging.getLogger(__name__)

_ROLE_FOR = {"error": "statusError", "warning": "statusWarning", "info": "statusInfo"}


def heading(text: str) -> QLabel:
    lbl = QLabel(text)
    set_role(lbl, "heading")
    return lbl


def make_kv_table() -> QTableWidget:
    t = QTableWidget(0, 2)
    t.setHorizontalHeaderLabels(["Property", "Value"])
    fit_table(t, stretch=[1], content=[0])
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return t


def fill_kv(table: QTableWidget, rows) -> None:
    table.setRowCount(len(rows))
    for r, (k, v) in enumerate(rows):
        table.setItem(r, 0, centered_item(str(k)))
        table.setItem(r, 1, centered_item(str(v)))


def make_dict_table(columns) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    fit_last(t)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return t


def fill_dict(table: QTableWidget, rows, columns) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, col in enumerate(columns):
            table.setItem(r, c, centered_item(str(row.get(col, ""))))


def add_findings(layout: QVBoxLayout, findings: List[ap.Finding], all_clear: str) -> None:
    if not findings:
        ok = QLabel(all_clear)
        set_role(ok, "statusSuccess")
        layout.addWidget(ok)
    for f in findings:
        lbl = QLabel(f"<b>{f.title}</b>. {f.detail}")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        set_role(lbl, _ROLE_FOR.get(f.severity, "statusInfo"))
        layout.addWidget(lbl)


# -- simple tabs -----------------------------------------------------------

def setup_kv(layout, data) -> None:
    t = make_kv_table()
    fill_kv(t, data)
    layout.addWidget(t)


def setup_dict(columns):
    def setup(layout, data):
        t = make_dict_table(columns)
        fill_dict(t, data, columns)
        layout.addWidget(t)
    return setup


def load_storage(worker=None):
    _wmi_drives, partitions = hr.get_storage_info()
    return ar.physical_drives(), partitions


def setup_storage(layout, data) -> None:
    drives, partitions = data
    layout.addWidget(heading("Physical Drives"))
    cols = ["Model", "Size", "Interface", "Serial", "Partitions"]
    t1 = make_dict_table(cols)
    fill_dict(t1, drives, cols)
    fit_table_height(t1)
    layout.addWidget(t1)
    layout.addWidget(heading("Partitions"))
    cols2 = ["Mount", "FS", "Total", "Used", "Free", "Use%"]
    t2 = make_dict_table(cols2)
    fill_dict(t2, partitions, cols2)
    layout.addWidget(t2, 1)


# -- memory ----------------------------------------------------------------

MEMORY_COLUMNS = ["Slot", "Bank", "Capacity", "Type", "Form", "Rated", "Configured",
                  "Manufacturer", "Part number", "Serial", "Note"]


def load_memory(worker=None):
    summary, _sticks = hr.get_memory_info()
    return summary, ar.read_memory_slots()


def _slot_cells(s: ap.MemorySlot):
    if not s.populated:
        return [centered_item(s.locator)] + [centered_item("") for _ in range(9)] + [centered_item("empty")]
    gb = s.capacity_bytes / 1024 ** 3
    ecc = "; ECC bits" if s.ecc_bits else ""
    return [
        centered_item(s.locator), centered_item(s.bank),
        numeric_item(f"{gb:.0f} GB", s.capacity_bytes), centered_item(s.mem_type),
        centered_item(s.form_factor),
        numeric_item(f"{s.rated_mhz} MHz" if s.rated_mhz else "n/a", s.rated_mhz),
        numeric_item(f"{s.configured_mhz} MHz" if s.configured_mhz else "n/a", s.configured_mhz),
        centered_item(s.manufacturer), centered_item(s.part_number), centered_item(s.serial),
        centered_item((s.speed_note + ecc).strip("; ")),
    ]


def setup_memory(layout, data) -> None:
    summary, report = data
    layout.addWidget(heading("Summary"))
    t1 = make_kv_table()
    fill_kv(t1, list(summary) + ([("Error correction", report.ecc_mode)] if report.ecc_mode else []))
    fit_table_height(t1)
    layout.addWidget(t1)
    layout.addWidget(heading(
        f"Slot map ({sum(1 for s in report.slots if s.populated)} of "
        f"{report.total_slots if report.total_slots is not None else '?'} slots populated)"))
    if report.error:
        note = QLabel(report.error)
        set_role(note, "statusWarning")
        layout.addWidget(note)
    t2 = QTableWidget(len(report.slots), len(MEMORY_COLUMNS))
    t2.setHorizontalHeaderLabels(MEMORY_COLUMNS)
    fit_table(t2, stretch=[10], content=list(range(10)))
    t2.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t2.verticalHeader().setVisible(False)
    t2.setSortingEnabled(False)
    for r, s in enumerate(report.slots):
        for c, item in enumerate(_slot_cells(s)):
            t2.setItem(r, c, item)
    fit_table_height(t2)
    layout.addWidget(t2)
    add_findings(layout, ap.memory_findings(report.slots), "Memory configuration looks consistent.")
    layout.addStretch(1)


# -- monitors --------------------------------------------------------------

MONITOR_COLUMNS = ["Make", "Model", "Serial", "Manufactured", "Size", "Status", "Registry instance"]


def load_monitors(worker=None):
    return ar.read_monitors()


def setup_monitors(layout, data) -> None:
    monitors, error = data
    layout.addWidget(heading("Monitors (from each panel's EDID)"))
    if error:
        note = QLabel(error)
        set_role(note, "statusWarning")
        layout.addWidget(note)
    t = QTableWidget(len(monitors), len(MONITOR_COLUMNS))
    t.setHorizontalHeaderLabels(MONITOR_COLUMNS)
    fit_table(t, stretch=[6], content=[0, 1, 2, 3, 4, 5])
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    for r, m in enumerate(monitors):
        status = {True: "Connected", False: "Remembered (not connected)", None: "Unknown"}[m.active]
        diag = m.diagonal_inches
        cells = [
            centered_item(m.make), centered_item(m.name or f"product {m.product_code}"),
            centered_item(m.serial or "not stored"), centered_item(m.manufactured or "n/a"),
            numeric_item(f'{diag:.1f}"' if diag else "n/a", diag), centered_item(status),
            centered_item(m.instance_path),
        ]
        paint_severity(cells[5], "ok" if m.active else "info")
        for c, item in enumerate(cells):
            t.setItem(r, c, item)
    fit_table_height(t)
    layout.addWidget(t)
    if not monitors:
        layout.addWidget(QLabel("No monitor EDID records were found."))
    hint = QLabel("A monitor's serial and manufacture date come from its own EDID, so they "
                  "identify the panel even when Windows calls it \"Generic PnP Monitor\".")
    hint.setWordWrap(True)
    set_role(hint, "infoNote")
    layout.addWidget(hint)
    layout.addStretch(1)


# -- firmware & security ---------------------------------------------------

def load_firmware(worker=None):
    fw = ar.read_firmware()
    battery, battery_note = ar.read_battery()
    return fw, battery, battery_note, hr.get_bios_info()


def setup_firmware(layout, data) -> None:
    fw, battery, battery_note, bios_rows = data
    layout.addWidget(heading("Firmware, boot security and battery"))
    t = make_kv_table()
    fill_kv(t, ap.firmware_rows(fw, battery, battery_note, bios_rows))
    fit_table_height(t, 20)
    layout.addWidget(t)
    layout.addWidget(heading("Needs attention"))
    findings = ap.firmware_findings(fw)
    if battery is not None and battery.wear_percent is not None and battery.wear_percent >= 40:
        findings.append(ap.Finding("warning", "Battery is worn",
                                   f"Holds {battery.health_percent}% of its design capacity."))
    add_findings(layout, findings, "Boot mode, Secure Boot, TPM and virtualization all look right.")
    layout.addStretch(1)


# -- asset record ----------------------------------------------------------

def load_asset(worker=None):
    return ar.read_asset_record()


class AssetRecordView(QWidget):
    """The flat asset record with copy/export buttons."""

    def __init__(self, record, findings, parent=None):
        super().__init__(parent)
        self.record = record
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.status = QLabel("")
        set_role(self.status, "muted")
        for label, fmt in (("Copy as Markdown", ap.record_to_markdown),
                           ("Copy as CSV", ap.record_to_csv), ("Copy as JSON", ap.record_to_json)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _c=False, f=fmt, name=label: self.copy(f, name))
            row.addWidget(btn)
        vendor, url = ap.warranty_lookup(record.get("Manufacturer", ""), record.get("Serial number", ""),
                                         record.get("Model", ""))
        self.warranty_btn = QPushButton(f"Open {vendor} warranty page" if vendor else "No warranty page known")
        self.warranty_btn.setEnabled(bool(url))
        self._url = url
        self.warranty_btn.setToolTip("Opens the vendor's own page in your browser; nothing is sent from here.")
        self.warranty_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._url)))
        row.addWidget(self.warranty_btn)
        row.addWidget(self.status, 1)
        box.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(ap.record_to_markdown(record))
        box.addWidget(self.text, 1)
        if findings:
            box.addWidget(heading("Worth noting"))
            add_findings(box, findings, "")

    def copy(self, fmt, name: str) -> None:
        QApplication.clipboard().setText(fmt(self.record))
        self.status.setText(f"{name}: copied.")


def setup_asset(layout, data) -> None:
    record, findings = data
    layout.addWidget(AssetRecordView(record, findings))

