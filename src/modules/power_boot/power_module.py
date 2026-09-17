import os
import subprocess
import winreg
import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTabWidget, QListWidget, QCheckBox, QSpinBox,
    QTableWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, fit_table
from core.widget_life import widget_is_valid as _widget_valid
from core.worker import Worker
from core.windows_utils import is_reboot_pending
import logging
logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


def _run(cmd, timeout=15):
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=CREATE_NO_WINDOW, timeout=timeout,
    )


# ── Power helpers ─────────────────────────────────────────────────────────────

def get_power_plans():
    """Returns list of (guid, name, active)."""
    r = _run(["powercfg", "/list"])
    plans = []
    for line in r.stdout.splitlines():
        if "Power Scheme GUID" in line or ("GUID" in line and "(" in line):
            active = "*" in line
            guid = ""
            name = ""
            parts = line.strip().split()
            for p in parts:
                if len(p) == 36 and p.count("-") == 4:
                    guid = p
            if "(" in line and ")" in line:
                start = line.index("(")
                end = line.rindex(")")
                name = line[start + 1:end]
            if guid:
                plans.append((guid, name, active))
    return plans


def set_active_plan(guid):
    _run(["powercfg", "/setactive", guid])


def get_hibernate_state():
    r = _run(["powercfg", "/a"])
    text = r.stdout.lower()
    return "hibernate" in text and "not available" not in text


def set_hibernate(enabled: bool):
    _run(["powercfg", "/hibernate", "on" if enabled else "off"])


def get_fast_startup():
    try:
        key = r"SYSTEM\CurrentControlSet\Control\Session Manager\Power"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            val, _ = winreg.QueryValueEx(k, "HiberbootEnabled")
            return bool(val)
    except OSError:
        return False


def set_fast_startup(enabled: bool):
    key = r"SYSTEM\CurrentControlSet\Control\Session Manager\Power"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "HiberbootEnabled", 0, winreg.REG_DWORD, int(enabled))


def get_sleep_timeout_ac():
    r = _run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"])
    for line in r.stdout.splitlines():
        if "Current AC Power Setting Index" in line:
            try:
                val = int(line.split(":")[-1].strip(), 16)
                return val // 60  # seconds → minutes
            except Exception:
                logger.warning("Ignored Exception", exc_info=True)
    return 0


def set_sleep_timeout(minutes: int):
    _run(["powercfg", "/change", "standby-timeout-ac", str(minutes)])
    _run(["powercfg", "/change", "standby-timeout-dc", str(minutes)])


# ── Boot helpers ──────────────────────────────────────────────────────────────

def get_boot_entries():
    r = _run(["bcdedit", "/enum"], timeout=10)
    entries = []
    current = {}
    for line in r.stdout.splitlines():
        line = line.rstrip()
        if not line:
            if current:
                entries.append(current)
                current = {}
        else:
            if line.startswith("Windows Boot") or line.startswith("Windows Legacy") or line.startswith("Firmware"):
                current = {"_section": line.strip()}
            else:
                key, _, val = line.partition("  ")
                key = key.strip()
                val = val.strip()
                if key and val:
                    current[key] = val
    if current:
        entries.append(current)
    return entries


def _bcd_backup():
    backup_dir = os.path.join(os.environ.get("APPDATA", ""), "WindowsTweaker", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, f"bcd_{ts}.bak")
    _run(["bcdedit", "/export", path])
    return path


def set_boot_timeout(seconds: int):
    _bcd_backup()
    _run(["bcdedit", "/timeout", str(seconds)])


def enable_safe_mode():
    """Set the Safe Mode boot flag. Returns the `bcdedit` CompletedProcess
    so a caller that needs to know whether it actually took — restarting
    into Safe Mode on an unset flag would silently boot normally — can
    check `returncode` rather than assume success. `_safe_set` (the button
    in this module) does not need it and keeps ignoring it."""
    _bcd_backup()
    return _run(["bcdedit", "/set", "{current}", "safeboot", "minimal"])


def disable_safe_mode():
    _bcd_backup()
    _run(["bcdedit", "/deletevalue", "{current}", "safeboot"])


# ── Helper ────────────────────────────────────────────────────────────────────

def _safe_set(fn, value, status_lbl):
    try:
        if value is None:
            fn()
        else:
            fn(value)
        status_lbl.setText("Done.")
    except Exception as e:
        status_lbl.setText(f"Error: {e}")


# ── Module ────────────────────────────────────────────────────────────────────

class PowerBootModule(BaseModule):
    name = "Power & Boot"
    icon = "⚡"
    description = "Power plans and boot configuration"
    requires_admin = True
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._worker: Optional[Worker] = None

    def create_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # Reboot pending banner
        reboot_banner = QLabel("⚠ A system reboot is pending.")
        reboot_banner.setStyleSheet(
            "background:#FF8800;color:white;padding:4px;font-weight:bold;"
        )
        try:
            reboot_banner.setVisible(is_reboot_pending())
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)
            reboot_banner.hide()
        layout.addWidget(reboot_banner)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        self._build_power_tab(tabs)
        self._build_boot_tab(tabs)

        return w

    def _build_power_tab(self, tabs: QTabWidget) -> None:
        power_w = QWidget()
        pw_layout = QVBoxLayout(power_w)

        plan_lbl = QLabel("Power Plans")
        plan_lbl.setStyleSheet("font-weight:bold;")
        pw_layout.addWidget(plan_lbl)

        plan_list = QListWidget()
        plan_list.setMaximumHeight(120)
        set_plan_btn = QPushButton("Set Active Plan")
        pw_layout.addWidget(plan_list)
        pw_layout.addWidget(set_plan_btn)

        toggle_lbl = QLabel("Power Toggles")
        toggle_lbl.setStyleSheet("font-weight:bold;margin-top:8px;")
        pw_layout.addWidget(toggle_lbl)

        fast_startup_cb = QCheckBox("Fast Startup")
        hibernate_cb = QCheckBox("Hibernate")
        pw_layout.addWidget(fast_startup_cb)
        pw_layout.addWidget(hibernate_cb)

        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("Sleep timeout (min, 0=never):"))
        sleep_spin = QSpinBox()
        sleep_spin.setRange(0, 9999)
        apply_sleep_btn = QPushButton("Apply")
        sleep_row.addWidget(sleep_spin)
        sleep_row.addWidget(apply_sleep_btn)
        sleep_row.addStretch()
        pw_layout.addLayout(sleep_row)

        status_lbl = QLabel("")
        pw_layout.addWidget(status_lbl)
        pw_layout.addStretch()

        plans_ref = [[]]  # mutable container: [(guid, name, active)]

        def load_power():
            set_plan_btn.setEnabled(False)
            status_lbl.setText("Loading...")

            def _run_load(_worker):
                plans = get_power_plans()
                hs = get_hibernate_state()
                fs = get_fast_startup()
                sl = get_sleep_timeout_ac()
                return plans, hs, fs, sl

            def on_result(data):
                if not _widget_valid(plan_list):
                    return
                plans, hs, fs, sl = data
                plans_ref[0] = plans
                plan_list.clear()
                for guid, name, active in plans:
                    item = QListWidgetItem(f"{'★ ' if active else ''}{name}")
                    item.setData(Qt.ItemDataRole.UserRole, guid)
                    plan_list.addItem(item)
                # Block signals to avoid triggering toggled callbacks while setting
                if _widget_valid(fast_startup_cb) and _widget_valid(hibernate_cb):
                    fast_startup_cb.blockSignals(True)
                    hibernate_cb.blockSignals(True)
                    fast_startup_cb.setChecked(fs)
                    hibernate_cb.setChecked(hs)
                    fast_startup_cb.blockSignals(False)
                    hibernate_cb.blockSignals(False)
                if _widget_valid(sleep_spin):
                    sleep_spin.setValue(sl)
                if _widget_valid(set_plan_btn):
                    set_plan_btn.setEnabled(True)
                if _widget_valid(status_lbl):
                    status_lbl.setText("Loaded.")

            def on_error(err):
                if _widget_valid(status_lbl):
                    status_lbl.setText(f"Error: {err}")
                if _widget_valid(set_plan_btn):
                    set_plan_btn.setEnabled(True)

            self._worker = Worker(_run_load)
            self._worker.signals.result.connect(on_result)
            self._worker.signals.error.connect(on_error)
            self._workers.append(self._worker)
            self.thread_pool.start(self._worker)

        def set_plan():
            item = plan_list.currentItem()
            if not item:
                return
            guid = item.data(Qt.ItemDataRole.UserRole)
            try:
                set_active_plan(guid)
                status_lbl.setText("Active plan set.")
                load_power()
            except Exception as e:
                status_lbl.setText(f"Error: {e}")

        fast_startup_cb.toggled.connect(
            lambda v: _safe_set(set_fast_startup, v, status_lbl)
        )
        hibernate_cb.toggled.connect(
            lambda v: _safe_set(set_hibernate, v, status_lbl)
        )
        apply_sleep_btn.clicked.connect(
            lambda: _safe_set(set_sleep_timeout, sleep_spin.value(), status_lbl)
        )
        set_plan_btn.clicked.connect(set_plan)

        tabs.addTab(power_w, "Power")
        self._load_power = load_power
        load_power()

    def _build_boot_tab(self, tabs: QTabWidget) -> None:
        boot_w = QWidget()
        bt_layout = QVBoxLayout(boot_w)

        toolbar = QHBoxLayout()
        refresh_boot_btn = QPushButton("Refresh")
        safe_on_btn = QPushButton("Enable Safe Mode")
        safe_off_btn = QPushButton("Disable Safe Mode")
        adv_startup_btn = QPushButton("Advanced Startup")
        boot_status = QLabel("")
        toolbar.addWidget(refresh_boot_btn)
        toolbar.addWidget(safe_on_btn)
        toolbar.addWidget(safe_off_btn)
        toolbar.addWidget(adv_startup_btn)
        toolbar.addStretch()
        toolbar.addWidget(boot_status)
        bt_layout.addLayout(toolbar)

        timeout_row = QHBoxLayout()
        timeout_spin = QSpinBox()
        timeout_spin.setRange(0, 999)
        timeout_spin.setSuffix(" sec")
        set_timeout_btn = QPushButton("Set Timeout")
        timeout_row.addWidget(QLabel("Boot timeout:"))
        timeout_row.addWidget(timeout_spin)
        timeout_row.addWidget(set_timeout_btn)
        timeout_row.addStretch()
        bt_layout.addLayout(timeout_row)

        boot_table = QTableWidget(0, 2)
        boot_table.setHorizontalHeaderLabels(["Property", "Value"])
        fit_table(boot_table, stretch=[1], content=[0])
        boot_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        bt_layout.addWidget(boot_table, 1)

        def load_boot():
            refresh_boot_btn.setEnabled(False)
            boot_status.setText("Loading...")

            self._worker = Worker(lambda _w: get_boot_entries())

            def on_result(entries):
                if not _widget_valid(boot_table):
                    return
                if _widget_valid(refresh_boot_btn):
                    refresh_boot_btn.setEnabled(True)
                if _widget_valid(boot_status):
                    boot_status.setText(f"{len(entries)} entries.")
                rows = []
                for entry in entries:
                    for k, v in entry.items():
                        rows.append((k, v))
                    rows.append(("---", "---"))
                boot_table.setRowCount(len(rows))
                for r, (k, v) in enumerate(rows):
                    boot_table.setItem(r, 0, centered_item(k))
                    boot_table.setItem(r, 1, centered_item(v))

            def on_error(err):
                if _widget_valid(refresh_boot_btn):
                    refresh_boot_btn.setEnabled(True)
                if _widget_valid(boot_status):
                    boot_status.setText(f"Error: {err}")

            self._worker.signals.result.connect(on_result)
            self._worker.signals.error.connect(on_error)
            self._workers.append(self._worker)
            self.thread_pool.start(self._worker)

        refresh_boot_btn.clicked.connect(load_boot)
        set_timeout_btn.clicked.connect(
            lambda: _safe_set(set_boot_timeout, timeout_spin.value(), boot_status)
        )
        safe_on_btn.clicked.connect(
            lambda: _safe_set(enable_safe_mode, None, boot_status)
        )
        safe_off_btn.clicked.connect(
            lambda: _safe_set(disable_safe_mode, None, boot_status)
        )
        adv_startup_btn.clicked.connect(
            lambda: subprocess.Popen(
                ["shutdown", "/r", "/o", "/f", "/t", "0"],
                creationflags=CREATE_NO_WINDOW,
            )
        )

        tabs.addTab(boot_w, "Boot")
        self._load_boot = load_boot
        load_boot()

    def on_start(self, app=None) -> None:
        pass

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        if hasattr(self, "_load_power"):
            self._load_power()
        if hasattr(self, "_load_boot"):
            self._load_boot()

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        self.cancel_all_workers()
