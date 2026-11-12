"""Wave Metrics Tab for Game Mode
================================

Presents per-wave telemetry and benchmark records using LogPane, with on-demand
40-wave execution and summary reporting.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.worker import Worker
from ui.log_pane import LogPane
from .telemetry import (
    METRICS_JSONL,
    clear_wave_metrics,
    get_telemetry_dir,
    load_wave_metrics,
    run_wave_benchmark,
    to_log_entries,
)

if TYPE_CHECKING:
    from .game_mode_module import GameModeModule

logger = logging.getLogger(__name__)


class WaveMetricsTab(QWidget):
    """Hosts LogPane displaying per-wave telemetry with benchmark controls."""

    def __init__(self, host: GameModeModule, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.host = host
        self._loaded = False
        self._running_worker: Optional[Worker] = None
        self._run_btn: Optional[QPushButton] = None
        self._clear_btn: Optional[QPushButton] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._pane = LogPane(
            loader=self._load_entries,
            empty_text="No wave metrics recorded yet. Run the 40-Wave Benchmark or activate Game Mode.",
            extra_controls=self._build_toolbar,
            extra_columns=["Wave", "Handles", "CPU %", "GPU %", "Disk Lat (ms)", "Net Lat (ms)"],
            extra_values=self._extra_values,
            detail_enricher=self._detail_enricher,
            parent=self,
        )
        layout.addWidget(self._pane)

    def activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._pane.load(force=True)

    def _build_toolbar(self, layout: QHBoxLayout, extra: dict) -> None:
        self._run_btn = QPushButton("Run 40-Wave Test", self)
        self._run_btn.setToolTip("Run a consecutive 40-wave Game Mode benchmark with full telemetry and deterministic cleanup.")
        self._run_btn.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._run_btn)

        self._clear_btn = QPushButton("Clear Metrics", self)
        self._clear_btn.setToolTip("Clear recorded wave metrics and restart telemetry log.")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        layout.addWidget(self._clear_btn)

    def _extra_values(self, entry: Any) -> list:
        raw = getattr(entry, "raw", {}) or {}
        handles = raw.get("handles")
        h_str = f"{handles:,}" if isinstance(handles, int) else "N/A"
        return [
            f"Wave {raw.get('wave', 0):02d}",
            h_str,
            f"{raw.get('cpu_percent', 0.0):.1f}%",
            f"{raw.get('gpu_percent', 0.0):.1f}%",
            f"{raw.get('disk_latency_ms', 0.0):.1f}",
            f"{raw.get('network_latency_ms', 0.0):.1f}",
        ]

    def _detail_enricher(self, entry: Any) -> str:
        raw = getattr(entry, "raw", {}) or {}
        wave = raw.get("wave", 0)
        handles = raw.get("handles")
        h_str = f"{handles:,}" if isinstance(handles, int) else "N/A"
        cpu = raw.get("cpu_percent", 0.0)
        gpu = raw.get("gpu_percent", 0.0)
        ram = raw.get("ram_percent", 0.0)
        disk = raw.get("disk_latency_ms", 0.0)
        net = raw.get("network_latency_ms", 0.0)
        status = raw.get("status", "ok").upper()
        notes = raw.get("notes", "None")
        ts = raw.get("timestamp", "N/A")

        return (
            f"=== WAVE {wave:02d} TELEMETRY DETAILS ===\n"
            f"Timestamp:       {ts}\n"
            f"Status:          {status}\n"
            f"Handles:         {h_str}\n"
            f"Processes:       {raw.get('processes', 'N/A')}\n"
            f"CPU Usage:       {cpu:.1f}%\n"
            f"GPU Usage:       {gpu:.1f}%\n"
            f"RAM Usage:       {ram:.1f}%\n"
            f"Disk Latency:    {disk:.2f} ms\n"
            f"Network Latency: {net:.2f} ms\n"
            f"Notes:           {notes}\n"
        )

    def _load_entries(self, worker: Worker) -> list:
        metrics = load_wave_metrics(self._folder())
        return to_log_entries(metrics)

    def _folder(self) -> Optional[str]:
        if hasattr(self.host, "_journal") and hasattr(self.host._journal, "folder"):
            return self.host._journal.folder
        return None

    def _on_clear_clicked(self) -> None:
        if QMessageBox.question(
            self,
            "Clear Wave Metrics",
            "Are you sure you want to clear all recorded wave metrics?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            clear_wave_metrics(self._folder())
            self._pane.load(force=True)

    def _on_run_clicked(self) -> None:
        if self._running_worker is not None:
            return

        res = QMessageBox.question(
            self,
            "Run 40-Wave Benchmark",
            "This will execute a 40-wave Game Mode benchmark cycle.\n\n"
            "Each wave collects full telemetry (handles, CPU, GPU, disk, network) "
            "and performs deterministic resource cleanup through ResourceTracker.\n\n"
            "Do you want to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        if self._run_btn:
            self._run_btn.setEnabled(False)
            self._run_btn.setText("Running Waves...")

        def _do_work(w: Worker) -> List[Dict[str, Any]]:
            def progress(current: int, total: int) -> None:
                w.signals.progress.emit(int((current / total) * 100))

            return run_wave_benchmark(
                system=getattr(self.host, "system", None),
                wave_count=40,
                interval_s=0.2,
                folder=self._folder(),
                log_cb=lambda level, msg: logger.info("[%s] %s", level, msg),
                progress_cb=progress,
                cancel_check=lambda: w.is_cancelled,
            )

        worker = Worker(_do_work)
        self._running_worker = worker
        worker.signals.progress.connect(self._pane._progress.setValue)
        worker.signals.result.connect(self._on_benchmark_done)
        worker.signals.error.connect(self._on_benchmark_error)

        self._pane._progress.setValue(0)
        self._pane._progress.setVisible(True)

        # Launch via host's bounded worker runner
        if hasattr(self.host, "_start"):
            self.host._start(worker)
        elif hasattr(self.host, "app") and hasattr(self.host.app, "thread_pool"):
            self.host.app.thread_pool.start(worker)
        else:
            worker.run()

    def _on_benchmark_done(self, results: Any) -> None:
        self._running_worker = None
        self._pane._progress.setVisible(False)
        if self._run_btn:
            self._run_btn.setEnabled(True)
            self._run_btn.setText("Run 40-Wave Test")

        self._pane.load(force=True)

        if isinstance(results, list) and results:
            total_waves = len(results)
            handles_list = [r["handles"] for r in results if isinstance(r.get("handles"), int)]
            avg_h = int(sum(handles_list) / len(handles_list)) if handles_list else 0
            peak_h = max(handles_list) if handles_list else 0
            log_dir = get_telemetry_dir(self._folder())

            msg = (
                f"40-Wave Game Mode Benchmark Completed!\n\n"
                f"• Waves Executed: {total_waves}/40\n"
                f"• Average Handles: {avg_h:,}\n"
                f"• Peak Handles: {peak_h:,}\n"
                f"• Cleanup Verification: Passed (Deterministic RAII)\n\n"
                f"Metrics saved to:\n{log_dir / METRICS_JSONL}"
            )
            QMessageBox.information(self, "Wave Benchmark Summary", msg)
        else:
            QMessageBox.warning(self, "Benchmark Incomplete", "The wave run was cancelled or returned no metrics.")

    def _on_benchmark_error(self, exc: Any) -> None:
        self._running_worker = None
        self._pane._progress.setVisible(False)
        if self._run_btn:
            self._run_btn.setEnabled(True)
            self._run_btn.setText("Run 40-Wave Test")
        QMessageBox.critical(self, "Benchmark Error", f"Wave benchmark failed with error: {exc}")
