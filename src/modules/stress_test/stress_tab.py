"""Stress controls and polling UI. Only the engine creates load."""
from dataclasses import asdict
import html
import logging
import os
import queue
import threading
import time

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.worker import Worker, retire
from .engine.ram_test import GIB, default_ram_bytes
from .engine.safety import SafetyPolicy
from .engine.session import SessionConfig, StressSession
from .stress_charts import SensorChart

logger = logging.getLogger(__name__)


class StressTab(QWidget):
    def __init__(self, module):
        super().__init__()
        self.module = module
        self._workers = []
        self._preview_started = False
        self._preview_busy = False
        self._preview_thread = None
        self._preview_readings = []
        self._extrema = {}
        self._last_plot = -1
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.poll)

    def _build(self):
        layout = QVBoxLayout(self)
        title = QLabel('Stress Test - CPU and RAM stability', self)
        layout.addWidget(title)
        note = QLabel('Integer/hash/compression load -- not an AVX floating-point torture like Linpack.\n'
                      'RAM testing covers only allocated memory, not memory Windows or other apps use. '
                      'MemTest86 at boot is the full test. No fan, clock or power-plan settings are changed.', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.controls = QWidget(self)
        form = QFormLayout(self.controls)
        modes = QWidget(self.controls)
        row = QHBoxLayout(modes)
        row.setContentsMargins(0, 0, 0, 0)
        self.cpu_mode = QCheckBox('CPU', modes)
        self.cpu_mode.setChecked(True)
        self.ram_mode = QCheckBox('RAM', modes)
        row.addWidget(self.cpu_mode)
        row.addWidget(self.ram_mode)
        row.addStretch()
        form.addRow('Load (select both for combined):', modes)
        self.threads = QSpinBox(self.controls)
        self.threads.setRange(1, os.cpu_count() or 1)
        self.threads.setValue(os.cpu_count() or 1)
        form.addRow('CPU threads:', self.threads)
        self.ram_size = QDoubleSpinBox(self.controls)
        self.ram_size.setDecimals(3)
        self.ram_size.setRange(0, 1048576)
        self.ram_size.setSingleStep(.25)
        self.ram_size.setSuffix(' GiB')
        self.ram_size.setValue(default_ram_bytes() / GIB)
        form.addRow('RAM target (256 MiB chunks):', self.ram_size)
        ram_note = QLabel('Default: 70% of available memory, preserving at least 2 GiB or 15% of total. '
                          'Allocation rechecks the reserve before every chunk; actual coverage is reported.', self.controls)
        ram_note.setWordWrap(True)
        form.addRow('', ram_note)
        self.duration = QComboBox(self.controls)
        for minutes in (5, 15, 30, 60):
            self.duration.addItem(f'{minutes} minutes', minutes * 60)
        form.addRow('Duration:', self.duration)
        self.cpu_limit = self._temperature(70, 95, 90)
        self.gpu_limit = self._temperature(70, 90, 90)
        self.hotspot_limit = self._temperature(90, 105, 105)
        form.addRow('CPU cut-off:', self.cpu_limit)
        form.addRow('GPU edge cut-off:', self.gpu_limit)
        form.addRow('GPU hotspot cut-off (if exposed):', self.hotspot_limit)
        self.ack = QCheckBox('I acknowledge running without CPU temperature protection (maximum 10 minutes)', self.controls)
        form.addRow('', self.ack)
        layout.addWidget(self.controls)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('Start', self)
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton('Stop', self)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        self.sensor_button = QPushButton('Refresh sensors', self)
        self.sensor_button.clicked.connect(self._preview)
        buttons.addWidget(self.sensor_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel('Ready. Open this tab to check available sensors.', self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.counters = QLabel('Elapsed 00:00 | Remaining 00:00 | Iterations/s 0 | CPU errors 0 | RAM passes/errors 0/0', self)
        self.counters.setWordWrap(True)
        layout.addWidget(self.counters)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(['Sensor', 'Value / availability', 'Min', 'Max', 'Cut-off'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(160)
        layout.addWidget(self.table)
        grid = QGridLayout()
        self.charts = {}
        from core.semantic_colors import chart as chart_colour
        for index, (kind, label, unit, scale) in enumerate([
                ('cpu_temp', 'CPU temperature (hottest)', 'C', (0, 110)),
                ('gpu_temp', 'GPU edge temperature (hottest)', 'C', (0, 110)),
                ('cpu_clock', 'CPU effective clock (mean)', 'MHz', None),
                ('package_power', 'CPU package power', 'W', None)]):
            # The shared, theme-aware chart palette -- no colours frozen in code.
            chart = SensorChart(label, unit, chart_colour(index), scale, self)
            self.charts[kind] = chart
            grid.addWidget(chart, index // 2, index % 2)
        layout.addLayout(grid)
        self.report_link = QLabel('', self)
        self.report_link.setOpenExternalLinks(True)
        self.report_link.setWordWrap(True)
        layout.addWidget(self.report_link)

    def _temperature(self, low, high, value):
        widget = QSpinBox(self.controls)
        widget.setRange(low, high)
        widget.setValue(value)
        widget.setSuffix(' C')
        return widget

    def activate(self):
        self._timer.start()
        if not self._preview_started and self.module._session is None:
            self._preview_started = True
            self._preview()
        self.poll()

    def _preview(self):
        if self._preview_busy or self.module._workers or (self.module._session and self.module._session.running):
            return
        if self._preview_thread and self._preview_thread.is_alive():
            self.status.setText('The previous sensor check is still returning.')
            return
        self._preview_busy = True
        self.start_button.setEnabled(False)
        self.sensor_button.setEnabled(False)
        self.status.setText('Checking sensors; no load is running.')
        adapter = self.module.make_sensors()
        mailbox = queue.Queue(maxsize=1)
        def read():
            try:
                mailbox.put((adapter.read(), ''))
            except Exception as exc:
                logger.warning('Stress preview failed: %s', exc)
                mailbox.put(([], str(exc)))
            finally:
                adapter.close()
        self._preview_thread = threading.Thread(target=read, name='StressSensorPreview', daemon=True)
        self._preview_thread.start()
        def wait(worker):
            deadline = time.monotonic() + 5
            while not worker.is_cancelled and time.monotonic() < deadline:
                try:
                    return mailbox.get(timeout=.05)
                except queue.Empty:
                    logger.debug('Stress: waiting for the sensor check')
                    continue
            return [], 'Sensor check did not finish within 5 s'
        worker = Worker(wait)
        self._workers.append(worker)
        worker.signals.result.connect(self._preview_done)
        worker.signals.error.connect(self._preview_error)
        worker.signals.finished.connect(self._preview_finished)
        QThreadPool.globalInstance().start(worker)

    def _preview_done(self, result):
        if sip.isdeleted(self):
            return
        readings, error = result
        self._preview_readings = [asdict(r) for r in readings]
        self._fill_sensors(self._preview_readings)
        self.status.setText(f'Sensor check failed: {error}' if error else 'Ready. Missing sensors and reasons are listed below.')

    def _preview_error(self, error):
        if not sip.isdeleted(self):
            self.status.setText(f'Sensor check failed: {error}')

    def _preview_finished(self):
        retire(self._workers)
        if not sip.isdeleted(self):
            self._preview_busy = False
            self.start_button.setEnabled(True)
            self.sensor_button.setEnabled(True)

    def start(self):
        if self._preview_busy:
            return
        if self._preview_thread and self._preview_thread.is_alive():
            self.status.setText('The sensor check is still blocked. Wait for it to return before starting load.')
            return
        try:
            policy = SafetyPolicy(duration_s=self.duration.currentData(), cpu_limit=self.cpu_limit.value(),
                gpu_limit=self.gpu_limit.value(), hotspot_limit=self.hotspot_limit.value(),
                acknowledge_no_cpu=self.ack.isChecked())
            config = SessionConfig(cpu=self.cpu_mode.isChecked(), ram=self.ram_mode.isChecked(),
                threads=self.threads.value(), ram_bytes=int(self.ram_size.value() * GIB), policy=policy)
            has_cpu = any(r['kind'] == 'cpu_temp' and r['value'] is not None for r in self._preview_readings)
            if not has_cpu and not policy.acknowledge_no_cpu:
                raise ValueError('No readable CPU temperature. Tick the explicit acknowledgement to run with a 10-minute cap.')
            duration = policy.duration_s if has_cpu else min(600, policy.duration_s)
            missing = [f'{r["name"]}: {r["reason"]}' for r in self._preview_readings if r['value'] is None]
            unavailable = '\n'.join(missing) or ('No preview readings; CPU temperature may be unavailable' if not self._preview_readings else 'None in the latest check')
            text = (f'CPU: {config.threads} threads' if config.cpu else 'CPU load: off')
            text += f'\nRAM: {config.ram_bytes / GIB:.3f} GiB' if config.ram else '\nRAM test: off'
            text += (f'\nDuration: {duration / 60:g} minutes (rechecked with fresh sensors at start)'
                     f'\nCut-offs: CPU {policy.cpu_limit:g} C, GPU edge {policy.gpu_limit:g} C, '
                     f'hotspot {policy.hotspot_limit:g} C; lost readable sensor >5 s.'
                     f'\n\nNot available on this PC:\n{unavailable}\n\n'
                     'This creates sustained load and can expose instability or crash Windows. '
                     'Save your work. Integer/hash/compression load, not Linpack AVX. '
                     'Testing continues when you leave this tab. Start?')
            answer = QMessageBox.warning(self, 'Start stability test', text,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Ok:
                return
            session = StressSession(config, self.module.make_sensors())
            self.module.start_session(session)
            self._extrema.clear()
            self._last_plot = -1
            self.report_link.clear()
            for chart in self.charts.values():
                chart.set_series([])
            self.charts['cpu_temp'].set_threshold(policy.cpu_limit)
            self.charts['gpu_temp'].set_threshold(policy.gpu_limit)
            self._timer.start()
            self.poll()
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))

    def stop(self):
        if self.module._session:
            self.module._session.stop()
            self.status.setText('Stopping all load; writing report...')

    @staticmethod
    def _time(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def poll(self):
        if sip.isdeleted(self):
            return
        session = self.module._session
        if session is None:
            return
        state = session.snapshot()
        active = session.running or bool(self.module._workers)
        self.controls.setEnabled(not active)
        self.start_button.setEnabled(not active and not self._preview_busy)
        self.stop_button.setEnabled(active)
        self.sensor_button.setEnabled(not active and not self._preview_busy)
        self.status.setText(f'{state["verdict"]}: {state["reason"]}')
        self._fill_sensors(state['readings'])
        cpu, ram = state['cpu'], state['ram']
        self.counters.setText(f'Elapsed {self._time(state["elapsed_s"])} | Remaining {self._time(state["remaining_s"])} | '
            f'Iterations/s {state["iterations_per_s"]:,.0f} | CPU errors {cpu.get("error_count", 0)} | '
            f'RAM passes/errors {ram.get("passes", 0)}/{ram.get("error_count", 0)} | '
            f'RAM allocated/tested {ram.get("allocated_bytes", 0) / GIB:.3f}/{ram.get("tested_bytes", 0) / GIB:.3f} GiB'
            + (f' | {ram["allocation_reason"]}' if ram.get('allocation_reason') else ''))
        tick = int(state['elapsed_s'])
        if tick != self._last_plot:
            self._last_plot = tick
            for kind, chart in self.charts.items():
                values = [r['value'] for r in state['readings'] if r['kind'] == kind and r['value'] is not None]
                chart.add_point(max(values) if values else None)
        if session.html_path:
            self.report_link.setText(f'<a href="{html.escape(session.html_path.resolve().as_uri(), quote=True)}">Open HTML report</a>'
                                     f' | <a href="{html.escape(session.json_path.resolve().as_uri(), quote=True)}">Open JSON report</a>')
        if not active and not self.isVisible():
            self._timer.stop()

    def _fill_sensors(self, readings):
        self.table.setRowCount(len(readings))
        limits = {'cpu_temp': self.cpu_limit.value(), 'gpu_temp': self.gpu_limit.value(),
                  'gpu_hotspot': self.hotspot_limit.value()}
        for row, reading in enumerate(readings):
            value = reading['value']
            if value is not None:
                previous = self._extrema.get(reading['key'], (value, value))
                self._extrema[reading['key']] = (min(previous[0], value), max(previous[1], value))
            bounds = self._extrema.get(reading['key'])
            unit = reading['unit']
            cells = [reading['name'], f'{value:.1f} {unit}' if value is not None else
                     f'not available on this PC: {reading["reason"] or "sensor did not answer"}',
                     f'{bounds[0]:.1f} {unit}' if bounds else '-', f'{bounds[1]:.1f} {unit}' if bounds else '-',
                     f'{limits[reading["kind"]]:g} C' if reading['kind'] in limits else '-']
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 360)

    def shutdown(self):
        self._timer.stop()
        retire(self._workers)

    def pause_idle(self):
        self._timer.stop()
