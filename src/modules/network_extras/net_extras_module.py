import logging

import subprocess
import winreg
import psutil
from PyQt6 import sip
from typing import List

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTabWidget, QLabel,
    QLineEdit, QComboBox, QCheckBox, QPlainTextEdit, QGridLayout)
from ui.help.network_ui import control_help, explain_tabs
from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from ui.help.network_overview import add_glance, feature_card, unreadable
logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
DNS_PRESETS = {
    "Google": ("8.8.8.8", "8.8.4.4"),
    "Cloudflare": ("1.1.1.1", "1.0.0.1"),
    "Quad9": ("9.9.9.9", "149.112.112.112"),
    "OpenDNS": ("208.67.222.222", "208.67.220.220"),
    "Custom": ("", ""),
}


def _read_overview():
    """Cheap read-only adapter and current-user proxy facts, independent of edits."""
    out = {'adapters': None, 'up': None, 'proxy': None, 'server': None}
    try:
        stats = psutil.net_if_stats()
        out.update(adapters=len(stats), up=sum(s.isup for s in stats.values()))
    except Exception as exc:
        out['adapter_error'] = str(exc)
        logger.warning('Could not read adapter summary: %s', exc)
    try:
        key = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            def read(name, default):
                try:
                    return winreg.QueryValueEx(handle, name)[0]
                except FileNotFoundError:
                    return default
            out.update(proxy=bool(read('ProxyEnable', 0)), server=str(read('ProxyServer', '')))
    except FileNotFoundError:
        out.update(proxy=False, server='')
    except OSError as exc:
        out['proxy_error'] = str(exc)
        logger.warning('Could not read proxy summary: %s', exc)
    return out


def run_cmd_stream(cmd: List[str], output_cb) -> int:
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace",
                                 creationflags=CREATE_NO_WINDOW)
        for line in proc.stdout:
            output_cb(line.rstrip())
        proc.wait()
        return proc.returncode
    except Exception as e:
        output_cb(f"Error: {e}")
        return -1


class NetExtrasModule(BaseModule):
    name = "Network Extras"
    icon = "🔌"
    description = "DNS switcher, proxy settings, quick network actions"
    requires_admin = True
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._widget = None
        self._loaded = False

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        self._glance = add_glance(layout, [
            ('adapters', 'Adapters up', 'info', 'Active adapters out of all adapters reported by Windows, including virtual adapters.'),
            ('proxy', 'User proxy', 'info', 'Current-user manual proxy enable setting. This does not describe WinHTTP or automatic proxy scripts.'),
            ('server', 'Proxy route', 'info', 'Saved manual proxy server when enabled; Direct means the manual proxy is off. Automatic configuration may still apply.'),
        ])
        tabs = QTabWidget()
        tabs.addTab(self._make_dns_tab(), "DNS Switcher")
        tabs.addTab(self._make_proxy_tab(), "Proxy Settings")
        tabs.addTab(self._make_quick_tab(), "Quick Actions")
        layout.addWidget(explain_tabs(tabs, "Quick Actions"), 1)
        self._widget = root
        return root

    def _show_overview(self, data):
        if self._widget is None or sip.isdeleted(self._widget):
            return
        adapters = data.get('adapters')
        self._glance['adapters'].set(f"{data['up']} / {adapters}" if adapters is not None else None,
                                      'Includes virtual adapters', reason=data.get('adapter_error', 'Could not read adapters'))
        enabled = data.get('proxy')
        self._glance['proxy'].set(('On' if enabled else 'Off') if enabled is not None else None,
                                  'Manual proxy setting', reason=data.get('proxy_error', 'Could not read proxy'))
        route = (data.get('server') or None) if enabled else 'Direct' if enabled is False else None
        self._glance['server'].set(route, 'Current-user manual proxy',
                                   reason=data.get('proxy_error', 'Proxy server not reported'))
    def _make_dns_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        card = feature_card('DNS for an adapter',
                            'Choose an active adapter and a resolver preset. Restore DHCP returns DNS selection to the network.')
        outer.addWidget(card)
        outer.addStretch()
        layout = card.layout()
        form = QHBoxLayout()
        adapter_combo = QComboBox()
        control_help(adapter_combo, 'adapter_combo')
        preset_combo = QComboBox()
        control_help(preset_combo, 'preset_combo')
        primary_edit = QLineEdit()
        control_help(primary_edit, 'primary_edit')
        secondary_edit = QLineEdit()
        control_help(secondary_edit, 'secondary_edit')
        apply_btn = QPushButton("Apply DNS")
        dhcp_btn = QPushButton("Restore DHCP")
        status = QLabel("")

        # Populate adapters
        try:
            stats = psutil.net_if_stats()
            for name, st in stats.items():
                if st.isup:
                    adapter_combo.addItem(name)
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

        # Populate presets
        for name in DNS_PRESETS:
            preset_combo.addItem(name)

        def on_preset_changed(text):
            if text in DNS_PRESETS:
                p, s = DNS_PRESETS[text]
                primary_edit.setText(p)
                secondary_edit.setText(s)
                primary_edit.setReadOnly(text != "Custom")
                secondary_edit.setReadOnly(text != "Custom")

        preset_combo.currentTextChanged.connect(on_preset_changed)
        on_preset_changed(preset_combo.currentText())

        form.addWidget(QLabel("Adapter:"))
        form.addWidget(adapter_combo)
        form.addWidget(QLabel("Preset:"))
        form.addWidget(preset_combo)
        form.addWidget(QLabel("Primary:"))
        form.addWidget(primary_edit)
        form.addWidget(QLabel("Secondary:"))
        form.addWidget(secondary_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(dhcp_btn)
        btn_row.addStretch()
        btn_row.addWidget(status)
        layout.addLayout(btn_row)
        layout.addStretch()

        def apply_dns():
            adapter = adapter_combo.currentText()
            primary = primary_edit.text().strip()
            secondary = secondary_edit.text().strip()
            if not adapter or not primary:
                status.setText("Select adapter and enter DNS.")
                return
            try:
                subprocess.run(["netsh", "interface", "ip", "set", "dns",
                                adapter, "static", primary],
                               capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)
                if secondary:
                    subprocess.run(["netsh", "interface", "ip", "add", "dns",
                                    adapter, secondary, "index=2"],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)
                status.setText(f"DNS set for {adapter}.")
            except Exception as e:
                status.setText(f"Error: {e}")

        def restore_dhcp():
            adapter = adapter_combo.currentText()
            try:
                subprocess.run(["netsh", "interface", "ip", "set", "dns",
                                adapter, "dhcp"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)
                status.setText(f"DHCP restored for {adapter}.")
            except Exception as e:
                status.setText(f"Error: {e}")

        apply_btn.clicked.connect(apply_dns)
        dhcp_btn.clicked.connect(restore_dhcp)
        return w

    def _make_proxy_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        card = feature_card('Current-user proxy',
                            'Load the saved proxy settings before editing. These settings apply to this Windows user.')
        outer.addWidget(card)
        outer.addStretch()
        layout = card.layout()
        PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

        enable_cb = QCheckBox("Enable Proxy")
        server_edit = QLineEdit()
        control_help(server_edit, 'server_edit')
        server_edit.setPlaceholderText("host:port")
        override_edit = QLineEdit()
        control_help(override_edit, 'override_edit')
        override_edit.setPlaceholderText("e.g. localhost;127.*;*.local")
        load_btn = QPushButton("Load")
        save_btn = QPushButton("Save")
        status = QLabel("")

        layout.addWidget(enable_cb)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Proxy Server:"))
        row1.addWidget(server_edit, 1)
        layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Bypass (override):"))
        row2.addWidget(override_edit, 1)
        layout.addLayout(row2)
        btn_row = QHBoxLayout()
        btn_row.addWidget(load_btn)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        btn_row.addWidget(status)
        layout.addLayout(btn_row)
        layout.addStretch()

        def load():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_KEY) as k:
                    def rv(name, default=""):
                        try:
                            return winreg.QueryValueEx(k, name)[0]
                        except Exception:
                            logger.warning("Ignored Exception", exc_info=True)
                            return default
                    enable_cb.setChecked(bool(rv("ProxyEnable", 0)))
                    server_edit.setText(str(rv("ProxyServer", "")))
                    override_edit.setText(str(rv("ProxyOverride", "")))
                status.setText("Loaded.")
            except Exception as e:
                status.setText(f"Error: {e}")

        def save():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_KEY,
                                    0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(enable_cb.isChecked()))
                    winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server_edit.text())
                    winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, override_edit.text())
                status.setText("Saved.")
            except Exception as e:
                status.setText(f"Error: {e}")

        load_btn.clicked.connect(load)
        save_btn.clicked.connect(save)
        return w

    def _make_quick_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        log = QPlainTextEdit()
        log.setReadOnly(True)

        grid = QGridLayout()
        actions = [
            ("Flush DNS", ["ipconfig", "/flushdns"]),
            ("Reset Winsock", ["netsh", "winsock", "reset"]),
            ("IP Release", ["ipconfig", "/release"]),
            ("IP Renew", ["ipconfig", "/renew"]),
            ("Reset TCP/IP", ["netsh", "int", "ip", "reset"]),
        ]
        for i, (label, cmd) in enumerate(actions):
            btn = QPushButton(label)
            btn_cmd = cmd[:]  # capture
            btn.clicked.connect(lambda _, c=btn_cmd: _run_action(c))
            hints = {'Flush DNS': 'Clear cached DNS answers so the next lookup asks again.',
                     'Reset Winsock': 'Reset the Winsock catalog. A restart is needed afterwards.',
                     'IP Release': 'Release DHCP leases. Network access may disconnect.',
                     'IP Renew': 'Ask DHCP for fresh IP configuration.',
                     'Reset TCP/IP': 'Reset TCP/IP configuration. A restart may be needed.'}
            card = feature_card(label, hints[label])
            card.layout().addWidget(btn)
            grid.addWidget(card, i // 3, i % 3)

        def _run_action(cmd):
            log.appendPlainText(f"\n> {' '.join(cmd)}")

            def _do_stream(worker):
                return run_cmd_stream(cmd, lambda line: worker.signals.log_line.emit(line))

            worker = Worker(_do_stream)
            worker.signals.log_line.connect(log.appendPlainText)
            worker.signals.result.connect(lambda _: log.appendPlainText("Done."))
            worker.signals.error.connect(lambda e: log.appendPlainText(f"Error: {e}"))
            self._workers.append(worker)
            self.thread_pool.start(worker)

        layout.addLayout(grid)
        layout.addWidget(log, 1)
        return w

    def on_start(self, app) -> None:
        self.app = app
    def on_stop(self) -> None:
        self.cancel_all_workers()
    def on_activate(self):
        if self._widget is None or self._loaded:
            return
        self._loaded = True
        worker = Worker(lambda _: _read_overview())
        worker.signals.result.connect(self._show_overview)
        worker.signals.error.connect(self._overview_failed)
        worker.signals.cancelled.connect(self._overview_cancelled)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _overview_failed(self, message):
        if self._widget is not None and not sip.isdeleted(self._widget):
            unreadable(self._glance, str(message))
        self._loaded = False
        logger.warning('Network Extras summary failed: %s', message)

    def _overview_cancelled(self):
        self._loaded = False
    def on_deactivate(self) -> None:
        self.cancel_all_workers()
