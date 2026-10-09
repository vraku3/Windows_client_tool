import datetime
import html as _html
import os
import platform
import socket

from typing import Optional

from PyQt6 import sip
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar,
    QFileDialog, QTextEdit,
)
from PyQt6.QtCore import QThreadPool

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from core.worker import COMWorker
import logging
logger = logging.getLogger(__name__)


def _collect_report_data(_worker) -> dict:
    import psutil
    import wmi

    c = wmi.WMI()
    data = {}

    # System
    data["hostname"] = socket.gethostname()
    data["os"] = platform.version()
    data["os_name"] = platform.system() + " " + platform.release()
    data["architecture"] = platform.machine()
    data["uptime_hours"] = round(
        (datetime.datetime.now().timestamp() - psutil.boot_time()) / 3600, 1
    )
    data["generated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # CPU
    cpu_list = c.Win32_Processor()
    if cpu_list:
        cpu = cpu_list[0]
        data["cpu_name"] = cpu.Name.strip() if cpu.Name else "Unknown"
        data["cpu_cores"] = getattr(cpu, "NumberOfCores", "?")
        data["cpu_threads"] = getattr(cpu, "NumberOfLogicalProcessors", "?")
    else:
        data["cpu_name"] = "Unknown"
        data["cpu_cores"] = data["cpu_threads"] = "?"
    data["cpu_percent"] = psutil.cpu_percent(interval=1)

    # Memory
    mem = psutil.virtual_memory()
    data["ram_total_gb"] = round(mem.total / 1024**3, 1)
    data["ram_used_gb"] = round(mem.used / 1024**3, 1)
    data["ram_percent"] = mem.percent

    # Disk
    disks = []
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "mount": part.mountpoint,
                "total_gb": round(usage.total / 1024**3, 1),
                "used_gb": round(usage.used / 1024**3, 1),
                "free_gb": round(usage.free / 1024**3, 1),
                "percent": usage.percent,
            })
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)
    data["disks"] = disks

    # GPU
    gpus = []
    for g in c.Win32_VideoController():
        ram_mb = round(int(g.AdapterRAM or 0) / 1024**2) if g.AdapterRAM else 0
        gpus.append({"name": g.Name or "?", "ram_mb": ram_mb})
    data["gpus"] = gpus

    # Network adapters
    adapters = []
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family.name == "AF_INET":
                adapters.append({"name": name, "ip": addr.address})
    data["adapters"] = adapters

    # Security
    try:
        import win32com.client
        wsc = win32com.client.GetObject("winmgmts:\\\\.\\root\\SecurityCenter2")
        av_list = [x.DisplayName for x in wsc.InstancesOf("AntiVirusProduct")]
        fw_list = [x.DisplayName for x in wsc.InstancesOf("FirewallProduct")]
        data["antivirus"] = ", ".join(av_list) if av_list else "None detected"
        data["firewall"] = ", ".join(fw_list) if fw_list else "None detected"
    except Exception:
        data["antivirus"] = "N/A"
        data["firewall"] = "N/A"

    # Top processes by memory
    procs = sorted(
        (p.info for p in psutil.process_iter(["name", "memory_percent"])
         if p.info["memory_percent"] is not None),
        key=lambda p: p["memory_percent"], reverse=True
    )[:10]
    data["top_procs"] = [
        {"name": p["name"], "mem_pct": round(p["memory_percent"], 1)}
        for p in procs
    ]

    # Software count from registry
    sw_count = 0
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for path in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ):
                try:
                    with winreg.OpenKey(hive, path) as k:
                        sw_count += winreg.QueryInfoKey(k)[0]
                except OSError:
                    logger.debug("Ignored OSError", exc_info=True)
    except Exception:
        logger.warning("Ignored Exception", exc_info=True)
    data["software_count"] = sw_count
    try:
        # Count NAMED entries, the same set the Software Inventory pane lists;
        # the raw key count above includes nameless helper keys.
        from modules.software_inventory.software_reader import fetch_software_inventory
        data["software_count"] = len(fetch_software_inventory())
    except OSError:
        logger.warning("Named software count failed; using the raw key count", exc_info=True)

    # Asset, disk, restore and software sections; each contains its own failure.
    from modules.system_report import report_sections
    data["sections"], data["findings"] = report_sections.collect_sections()

    return data


def _render_html(data: dict) -> str:
    def _bar(pct, color="#3498db"):
        return (
            f'<div style="background:#333;border-radius:4px;height:12px;width:200px;display:inline-block;">'
            f'<div style="background:{color};width:{pct}%;height:12px;border-radius:4px;"></div></div>'
            f' <span>{pct}%</span>'
        )

    disk_rows = "".join(
        f"<tr><td>{_html.escape(d['mount'])}</td><td>{d['total_gb']} GB</td>"
        f"<td>{d['used_gb']} GB</td><td>{d['free_gb']} GB</td>"
        f"<td>{_bar(d['percent'])}</td></tr>"
        for d in data.get("disks", [])
    )
    gpu_rows = "".join(
        f"<tr><td>{_html.escape(g['name'])}</td><td>{g['ram_mb']} MB</td></tr>"
        for g in data.get("gpus", [])
    )
    adapter_rows = "".join(
        f"<tr><td>{_html.escape(a['name'])}</td><td>{_html.escape(a['ip'])}</td></tr>"
        for a in data.get("adapters", [])
    )
    proc_rows = "".join(
        f"<tr><td>{_html.escape(str(p['name']))}</td><td>{p['mem_pct']}%</td></tr>"
        for p in data.get("top_procs", [])
    )

    from modules.system_report import report_sections as rs
    findings_block = rs.findings_html(data.get("findings", []))
    extra_html = rs.sections_html(data.get("sections", []))

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>System Report — {data.get('hostname','')}</title>
<style>
  body {{font-family:Segoe UI,Arial,sans-serif;background:#1e1e1e;color:#ccc;margin:20px;}}
  h1 {{color:#fff;}} h2 {{color:#3498db;border-bottom:1px solid #333;padding-bottom:4px;}}
  table {{border-collapse:collapse;width:100%;margin-bottom:16px;}}
  th {{background:#2c2c2c;color:#aaa;text-align:left;padding:6px 10px;}}
  td {{padding:5px 10px;border-bottom:1px solid #2c2c2c;}}
  .kv td:first-child {{color:#aaa;width:220px;}} .badge {{background:#2c2c2c;border-radius:4px;padding:2px 8px;}}
</style>
</head>
<body>
<h1>&#x1f4bb; System Report</h1>
<p>Generated: {data.get('generated','')} &nbsp;|&nbsp; Host: <b>{data.get('hostname','')}</b></p>
{findings_block}
<h2>System</h2>
<table class="kv">
<tr><td>OS</td><td>{data.get('os_name','')} ({data.get('architecture','')})</td></tr>
<tr><td>Version</td><td>{data.get('os','')}</td></tr>
<tr><td>Uptime</td><td>{data.get('uptime_hours',0)} hours</td></tr>
<tr><td>Installed software</td><td>{data.get('software_count',0)} packages</td></tr>
</table>
<h2>CPU</h2>
<table class="kv">
<tr><td>Name</td><td>{data.get('cpu_name','')}</td></tr>
<tr><td>Cores / Threads</td><td>{data.get('cpu_cores','?')} / {data.get('cpu_threads','?')}</td></tr>
<tr><td>Current load</td><td>{_bar(data.get('cpu_percent',0))}</td></tr>
</table>
<h2>Memory</h2>
<table class="kv">
<tr><td>Total RAM</td><td>{data.get('ram_total_gb',0)} GB</td></tr>
<tr><td>Used</td><td>{data.get('ram_used_gb',0)} GB &nbsp; {_bar(data.get('ram_percent',0),'#e67e22')}</td></tr>
</table>
<h2>Storage</h2>
<table><tr><th>Mount</th><th>Total</th><th>Used</th><th>Free</th><th>Usage</th></tr>
{disk_rows}</table>
<h2>GPU</h2>
<table><tr><th>Name</th><th>VRAM</th></tr>{gpu_rows}</table>
<h2>Network Adapters</h2>
<table><tr><th>Adapter</th><th>IP Address</th></tr>{adapter_rows}</table>
<h2>Security</h2>
<table class="kv">
<tr><td>Antivirus</td><td>{data.get('antivirus','')}</td></tr>
<tr><td>Firewall</td><td>{data.get('firewall','')}</td></tr>
</table>
<h2>Top Processes by Memory</h2>
<table><tr><th>Process</th><th>Memory %</th></tr>{proc_rows}</table>
{extra_html}
</body></html>"""


class SystemReportModule(BaseModule):
    name = "System Report"
    icon = "📋"
    description = "System report: hardware, disks, restore points, software and computed findings; HTML or Markdown"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(12, 12, 12, 12)

        info_label = QLabel(
            "Collects CPU, memory, crash and unexpected-shutdown history, Windows patch level, the System Health checks, "
            "disks and volumes (health, TRIM, BitLocker), restore points and "
            "shadow storage, memory slots, monitors, firmware and security state, installed software "
            "(runtimes, duplicates, end-of-life) and top processes, with a computed \"needs attention\" list. "
            "Each section is read independently; one that cannot be read says so."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        btn_row = QHBoxLayout()
        self._gen_btn = QPushButton("Generate Report")
        self._save_btn = QPushButton("Save HTML...")
        self._copy_btn = QPushButton("Copy as Markdown")
        self._copy_btn.setToolTip("Ticket-ready Markdown of the whole report")
        self._save_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)
        self._status_label = QLabel("")
        set_role(self._status_label, "muted")
        for w in (self._gen_btn, self._save_btn, self._copy_btn, self._status_label):
            btn_row.addWidget(w)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setPlaceholderText("Press Generate Report; the report appears here.")
        layout.addWidget(self._preview, 1)

        self._outer = outer
        self._data: Optional[dict] = None
        self._gen_btn.clicked.connect(self._do_generate)
        self._save_btn.clicked.connect(self._save_html)
        self._copy_btn.clicked.connect(self._copy_markdown)
        return outer

    def _do_generate(self):
        self._gen_btn.setEnabled(False)
        self._status_label.setText("Collecting data (about ten seconds)...")
        self._progress.show()
        worker = COMWorker(_collect_report_data)
        worker.signals.result.connect(lambda data: self._on_data(data))
        worker.signals.error.connect(lambda err: self._on_error(err))
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_data(self, data: dict):
        if sip.isdeleted(self._outer):
            return
        self._data = data
        self._preview.setHtml(_render_html(data))
        self._gen_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._copy_btn.setEnabled(True)
        self._progress.hide()
        flagged = sum(1 for f in data.get("findings", []) if f.severity in ("error", "warning"))
        self._status_label.setText(f"Generated {data.get('generated', '')}: {flagged} item(s) need attention.")

    def _save_html(self):
        if self._data is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._outer, "Save Report", f"system_report_{self._data.get('hostname', 'pc')}.html", "HTML (*.html)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_render_html(self._data))
        except OSError as e:
            self._status_label.setText(f"Save error: {e}")
            logger.warning("Report save failed: %s", e)
            return
        self._status_label.setText(f"Saved to {os.path.basename(path)}")
        os.startfile(path)

    def _copy_markdown(self):
        if self._data is None:
            return
        from modules.system_report import report_sections
        QApplication.clipboard().setText(report_sections.build_markdown(self._data))
        self._status_label.setText("Copied the report as Markdown.")

    def _on_error(self, err: str):
        if sip.isdeleted(self._outer):
            return
        self._gen_btn.setEnabled(True)
        self._progress.hide()
        self._status_label.setText(f"Error: {err}")

    def on_start(self, app): self.app = app
    def on_stop(self): self.cancel_all_workers()
    def on_activate(self): pass
    def on_deactivate(self): self.cancel_all_workers()
