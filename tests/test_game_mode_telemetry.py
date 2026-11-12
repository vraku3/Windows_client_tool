"""Tests for modules.game_mode.telemetry and WaveMetricsTab."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.game_mode import telemetry
from modules.game_mode.telemetry import (
    METRICS_JSONL,
    METRICS_LOG,
    clear_wave_metrics,
    collect_system_telemetry,
    load_wave_metrics,
    record_wave_metric,
    run_wave_benchmark,
    to_log_entries,
)


def test_collect_system_telemetry_fake():
    class FakeProcess:
        def __init__(self, handles):
            self.handles = handles

    class FakeSystem:
        def processes(self):
            return [FakeProcess(100), FakeProcess(200)]

    data = collect_system_telemetry(FakeSystem())
    assert isinstance(data, dict)
    assert data["handles"] == 300
    assert data["processes"] == 2
    assert "cpu_percent" in data
    assert "disk_latency_ms" in data
    assert "network_latency_ms" in data


def test_record_and_load_wave_metrics(tmp_path):
    clear_wave_metrics(str(tmp_path))

    data1 = {"handles": 120000, "processes": 200, "cpu_percent": 15.5,
             "gpu_percent": 45.0, "disk_latency_ms": 1.2, "network_latency_ms": 12.4}
    record_wave_metric(1, data1, folder=str(tmp_path), notes="Wave 1 ok")

    data2 = {"handles": 115000, "processes": 195, "cpu_percent": 12.0,
             "gpu_percent": 50.0, "disk_latency_ms": 0.8, "network_latency_ms": 11.9}
    record_wave_metric(2, data2, folder=str(tmp_path), notes="Wave 2 ok")

    # Check files exist
    assert (tmp_path / METRICS_JSONL).exists()
    assert (tmp_path / METRICS_LOG).exists()

    records = load_wave_metrics(str(tmp_path))
    assert len(records) == 2
    assert records[0]["wave"] == 1
    assert records[0]["handles"] == 120000
    assert records[1]["wave"] == 2
    assert records[1]["handles"] == 115000

    # Convert to LogEntry
    entries = to_log_entries(records)
    assert len(entries) == 2
    assert entries[0].source == "Wave 01"
    assert "Handles: 120,000" in entries[0].message
    assert entries[1].source == "Wave 02"

    # Test clear
    clear_wave_metrics(str(tmp_path))
    assert not (tmp_path / METRICS_JSONL).exists()
    assert not (tmp_path / METRICS_LOG).exists()
    assert load_wave_metrics(str(tmp_path)) == []


def test_run_wave_benchmark_execution(tmp_path):
    class FakeSystem:
        def processes(self): return []

    progress_history = []
    log_history = []

    results = run_wave_benchmark(
        system=FakeSystem(),
        wave_count=5,
        interval_s=0.01,
        folder=str(tmp_path),
        log_cb=lambda lvl, msg: log_history.append((lvl, msg)),
        progress_cb=lambda cur, tot: progress_history.append((cur, tot)),
    )

    assert len(results) == 5
    assert len(progress_history) == 5
    assert progress_history[-1] == (5, 5)
    assert any("Starting" in msg for _lvl, msg in log_history)
    assert any("Completed" in msg for _lvl, msg in log_history)

    # Check that 5 snapshots were recorded and cleaned up
    records = load_wave_metrics(str(tmp_path))
    assert len(records) == 5


def test_run_wave_benchmark_cancellation(tmp_path):
    class FakeSystem:
        def processes(self): return []

    cancelled = False
    def is_cancelled():
        nonlocal cancelled
        return cancelled

    def on_progress(cur, tot):
        nonlocal cancelled
        if cur >= 3:
            cancelled = True

    results = run_wave_benchmark(
        system=FakeSystem(),
        wave_count=10,
        interval_s=0.01,
        folder=str(tmp_path),
        progress_cb=on_progress,
        cancel_check=is_cancelled,
    )

    # Should cancel early after wave 3
    assert len(results) == 3


def test_wave_metrics_tab_ui(qapp):
    from modules.game_mode.wave_metrics_tab import WaveMetricsTab

    host = MagicMock()
    host._journal = MagicMock()
    host._journal.folder = None
    tab = WaveMetricsTab(host)
    assert tab._run_btn is not None
    assert tab._clear_btn is not None
    assert tab._pane is not None

    # Test extra values formatter
    entry = MagicMock()
    entry.raw = {
        "wave": 5,
        "handles": 135000,
        "cpu_percent": 18.5,
        "gpu_percent": 60.2,
        "disk_latency_ms": 1.4,
        "network_latency_ms": 15.0,
    }
    vals = tab._extra_values(entry)
    assert vals[0] == "Wave 05"
    assert vals[1] == "135,000"
    assert "18.5%" in vals[2]
    assert "60.2%" in vals[3]

    # Test detail enricher
    details = tab._detail_enricher(entry)
    assert "WAVE 05 TELEMETRY DETAILS" in details
    assert "135,000" in details

    tab.activate()
    assert tab._loaded is True
