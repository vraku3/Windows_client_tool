"""Tab bodies for the Hardware Info pane (Qt side).

Each ``setup_*`` receives the tab's content layout and the loader's result.
The loaders run on a COMWorker; the setups run on the UI thread.
"""

from modules.hardware_inventory.help_widgets import (
    describe_control, describe_headers, describe_cell, explain_widget,
)
import logging
from typing import List

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QTableWidget, QVBoxLayout, QWidget,
)

from core.semantic_colors import chrome, semantic
from core.table_ui import centered_item, fit_last, fit_table, set_role
from modules.hardware_inventory import asset_parse as ap
from modules.hardware_inventory import asset_reader as ar
from modules.hardware_inventory import device_reader as dvr
from modules.hardware_inventory import hardware_reader as hr
from ui.table_items import fit_table_height, numeric_item, paint_severity
from ui.glance import GlanceTile
from ui.table_style import apply_table_style

logger = logging.getLogger(__name__)

_ROLE_FOR = {"error": "statusError", "warning": "statusWarning", "info": "statusInfo"}


def heading(text: str) -> QLabel:
    lbl = QLabel(text)
    set_role(lbl, "heading")
    return lbl


def make_kv_table() -> QTableWidget:
    t = QTableWidget(0, 2)
    t.setHorizontalHeaderLabels(["Property", "Value"])
    describe_headers(t)
    fit_table(t, stretch=[1], content=[0])
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    apply_table_style(t)
    return t


def fill_kv(table: QTableWidget, rows) -> None:
    table.setRowCount(len(rows))
    for r, (k, v) in enumerate(rows):
        k_str = str(k)
        v_str = str(v)
        table.setItem(r, 0, centered_item(k_str))
        describe_cell(table, 0, table.item(r, 0))
        val_item = centered_item(v_str)
        v_low = v_str.strip().lower()
        if v_low in ("enabled", "yes", "true", "ok", "active", "pass", "passed", "running", "good"):
            paint_severity(val_item, "ok")
        elif v_low in ("disabled", "no", "false", "off", "inactive", "n/a", "none"):
            val_item.setForeground(QBrush(QColor(chrome("text_muted"))))
        elif v_low in ("failed", "error", "critical", "bad"):
            paint_severity(val_item, "error")
        elif v_low in ("warning", "caution", "degraded"):
            paint_severity(val_item, "warning")
        table.setItem(r, 1, val_item)
        describe_cell(table, 1, table.item(r, 1))


def make_dict_table(columns) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    describe_headers(t)
    fit_last(t)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    apply_table_style(t, size_columns=tuple(i for i, c in enumerate(columns)
                      if c in ("Size", "Total", "Used", "Free", "Use%", "RAM", "Speed")))
    return t


def fill_dict(table: QTableWidget, rows, columns) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, col in enumerate(columns):
            val = str(row.get(col, ""))
            item = centered_item(val)
            val_low = val.strip().lower()
            if col == "Up":
                if val_low == "yes":
                    paint_severity(item, "ok")
                elif val_low == "no":
                    item.setForeground(QBrush(QColor(chrome("text_muted"))))
            elif col == "Status":
                if val_low in ("ok", "good"):
                    paint_severity(item, "ok")
                elif any(m in val_low for m in ("error", "failed", "critical", "bad")):
                    paint_severity(item, "error")
                elif any(m in val_low for m in ("warn", "caution", "problem", "degraded")):
                    paint_severity(item, "warning")
                elif val_low in ("—", "-", "unknown", "n/a"):
                    item.setForeground(QBrush(QColor(chrome("text_muted"))))
            elif col == "Use%":
                try:
                    pct = float(val.rstrip("%"))
                    if pct >= 90:
                        paint_severity(item, "error")
                    elif pct >= 75:
                        paint_severity(item, "warning")
                    else:
                        paint_severity(item, "ok")
                except ValueError:
                    logger.debug("Non-numeric partition use percentage: %r", val)
            table.setItem(r, c, item)
            describe_cell(table, c, table.item(r, c))


def add_findings(layout: QVBoxLayout, findings: List[ap.Finding], all_clear: str) -> None:
    if not findings:
        ok = QLabel(f"✅  {all_clear}")
        set_role(ok, "statusSuccess")
        layout.addWidget(ok)
    for f in findings:
        icon = "⛔  " if f.severity in ("error", "critical") else ("⚠️  " if f.severity == "warning" else "ℹ️  ")
        lbl = QLabel(f"{icon}<b>{f.title}</b>: {f.detail}")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        set_role(lbl, _ROLE_FOR.get(f.severity, "statusInfo"))
        layout.addWidget(lbl)


# -- simple tabs -----------------------------------------------------------

def load_overview(worker=None):
    """Read-only card data; absent properties stay absent rather than becoming zero."""
    overview = dict(hr.get_overview(worker))
    cpu = dict(hr.get_cpu_info(worker))
    memory, _ = hr.get_memory_info(worker)
    gpus = hr.get_gpu_info(worker)
    bios = hr.get_bios_info(worker)
    monitors, error = ar.read_monitors()
    network = hr.get_network_info(worker)
    return {
        "CPU": [("Model", overview.get("CPU") or cpu.get("Model", "")),
                ("Cores", cpu.get("Physical Cores", "")),
                ("Threads", cpu.get("Logical Cores", ""))],
        "Memory": [("Type", "System memory"), ("Total", overview.get("RAM Total", "")), *memory] if memory or overview.get("RAM Total") else [],
        "GPU": [("Model", g.get("Name", "")) for g in gpus]
               + [(key, g.get(source, "")) for g in gpus
                  for key, source in (("VRAM", "RAM"), ("Driver", "Driver Version"))],
        "Board / BIOS": [("Model", overview.get("Model", "")), *bios],
        "Disks": [("Type", "Fixed storage"), ("Total", overview.get("Disk Total", "")),
                  ("Free", overview.get("Disk Free", ""))] if overview.get("Disk Total") else [],
        "Monitors": [("Model", m.name or m.make)
                     for m in monitors if m.active is not None] if not error else [],
        "Network": [(key, n[source]) for n in network if n["Up"] == "Yes"
                    for key, source in (("Adapter", "Name"), ("IP", "IP"), ("Speed", "Speed"))],
    }


def setup_overview(layout, data):
    from PyQt6.QtWidgets import QGridLayout
    grid = QGridLayout()
    grid.setSpacing(12)
    for index, name in enumerate(("CPU", "Memory", "GPU", "Board / BIOS", "Disks", "Monitors", "Network")):
        rows = [(str(k), str(v)) for k, v in data.get(name, [])
                if v and str(v) not in ("N/A", "None", "Unknown")]
        tile = GlanceTile(name, tooltip=f"{name}: measured inventory. Open its tab for the full details.")
        tile.value.setWordWrap(True)
        tile.sub.setWordWrap(True)
        identity = next(((k, v) for k, v in rows if k.lower() in
                         ("model", "name", "adapter", "type", "device")), None)
        primary = identity[1] if identity else (rows[0][1] if rows and name in ("Memory", "Disks") else None)
        tile.set(primary,
                 "\n".join(f"{k}: {v}" for k, v in rows if (k, v) != identity),
                 reason="Not reported by the inventory reader")
        tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tile.setCursor(Qt.CursorShape.ArrowCursor)
        grid.addWidget(tile, index // 2, index % 2)
    layout.addLayout(grid)
    layout.addStretch(1)

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


# -- USB and PCI devices ----------------------------------------------------

DEVICE_COLUMNS = ["Name", "Vendor", "Class", "Status", "Device ID"]


def load_devices(worker=None):
    return dvr.read_usb_devices(), dvr.read_pci_devices()


def _device_rows(devices):
    return [{"Name": d.name, "Vendor": d.vendor or "—", "Class": d.pnp_class,
             "Status": d.status or "—", "Device ID": d.device_id} for d in devices]


def setup_devices(layout, data) -> None:
    usb, pci = data
    problems = dvr.problem_devices(usb, pci)
    layout.addWidget(heading(f"USB devices ({len(usb) if usb is not None else 0})"
                             if usb is not None else "USB devices — could not be read"))
    if usb is not None:
        t1 = make_dict_table(DEVICE_COLUMNS)
        fill_dict(t1, _device_rows(usb), DEVICE_COLUMNS)
        fit_table_height(t1)
        layout.addWidget(t1)
    layout.addWidget(heading(f"PCI devices ({len(pci) if pci is not None else 0})"
                             if pci is not None else "PCI devices — could not be read"))
    if pci is not None:
        t2 = make_dict_table(DEVICE_COLUMNS)
        fill_dict(t2, _device_rows(pci), DEVICE_COLUMNS)
        layout.addWidget(t2, 1)
    if problems:
        layout.addWidget(heading(f"Windows flags {len(problems)} of these as a problem"))
        for d in problems:
            lbl = QLabel(f"<b>{d.name}</b>  ({d.status})")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            set_role(lbl, "statusWarning")
            layout.addWidget(lbl)


# -- memory ----------------------------------------------------------------

MEMORY_COLUMNS = ["Slot", "Bank", "Capacity", "Type", "Form", "Rated", "Configured",
                  "Manufacturer", "Part number", "Serial", "Note"]


def load_memory(worker=None):
    summary, _sticks = hr.get_memory_info()
    return summary, ar.read_memory_slots()


def _slot_cells(s: ap.MemorySlot):
    if not s.populated:
        empty_item = centered_item("empty")
        empty_item.setForeground(QBrush(QColor(chrome("text_muted"))))
        return [centered_item(s.locator)] + [centered_item("") for _ in range(9)] + [empty_item]
    gb = s.capacity_bytes / 1024 ** 3
    ecc = "; ECC bits" if s.ecc_bits else ""
    speed_item = centered_item((s.speed_note + ecc).strip("; "))
    if s.speed_note:
        speed_item.setForeground(QBrush(QColor(semantic("warning"))))
    return [
        centered_item(s.locator), centered_item(s.bank),
        numeric_item(f"{gb:.0f} GB", s.capacity_bytes), centered_item(s.mem_type),
        centered_item(s.form_factor),
        numeric_item(f"{s.rated_mhz} MHz" if s.rated_mhz else "n/a", s.rated_mhz),
        numeric_item(f"{s.configured_mhz} MHz" if s.configured_mhz else "n/a", s.configured_mhz),
        centered_item(s.manufacturer), centered_item(s.part_number), centered_item(s.serial),
        speed_item,
    ]


def setup_memory(layout, data) -> None:
    summary, report = data
    layout.addWidget(heading("Summary"))
    extra = []
    if report.ecc_mode:
        extra.append(("Error correction", report.ecc_mode))
    extra.append(("Max capacity (motherboard)", ap.max_capacity_text(report.max_capacity_bytes)))
    t1 = make_kv_table()
    fill_kv(t1, list(summary) + extra)
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
    describe_headers(t2)
    fit_table(t2, stretch=[10], content=list(range(10)))
    t2.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t2.verticalHeader().setVisible(False)
    t2.setSortingEnabled(False)
    for r, s in enumerate(report.slots):
        for c, item in enumerate(_slot_cells(s)):
            t2.setItem(r, c, item)
            describe_cell(t2, c, t2.item(r, c))
    fit_table_height(t2)
    layout.addWidget(t2)
    add_findings(layout, ap.memory_findings(report.slots, report.max_capacity_bytes),
                "Memory configuration looks consistent.")
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
    describe_headers(t)
    fit_table(t, stretch=[6], content=[0, 1, 2, 3, 4, 5])
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    for r, m in enumerate(monitors):
        status = {True: "Connected", False: "Remembered (not connected)", None: "Unknown"}[m.active]
        diag = m.diagonal_inches
        status_item = centered_item(status)
        if m.active:
            paint_severity(status_item, "ok")
        else:
            status_item.setForeground(QBrush(QColor(chrome("text_muted"))))
        cells = [
            centered_item(m.make), centered_item(m.name or f"product {m.product_code}"),
            centered_item(m.serial or "not stored"), centered_item(m.manufactured or "n/a"),
            numeric_item(f'{diag:.1f}"' if diag else "n/a", diag), status_item,
            centered_item(m.instance_path),
        ]
        for c, item in enumerate(cells):
            t.setItem(r, c, item)
            describe_cell(t, c, t.item(r, c))
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

def load_asset(worker=None, app_data_dir=None):
    return ar.read_asset_record(app_data_dir)


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
            describe_control(btn, 'btn')
            btn.clicked.connect(lambda _c=False, f=fmt, name=label: self.copy(f, name))
            row.addWidget(btn)
        vendor, url = ap.warranty_lookup(record.get("Manufacturer", ""), record.get("Serial number", ""),
                                         record.get("Model", ""))
        self.warranty_btn = QPushButton(f"Open {vendor} warranty page" if vendor else "No warranty page known")
        describe_control(self.warranty_btn, 'warranty_btn')
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
        explain_widget(self)

    def copy(self, fmt, name: str) -> None:
        QApplication.clipboard().setText(fmt(self.record))
        self.status.setText(f"{name}: copied.")
        set_role(self.status, "statusSuccess")


def setup_asset(layout, data) -> None:
    record, findings = data
    layout.addWidget(AssetRecordView(record, findings))

