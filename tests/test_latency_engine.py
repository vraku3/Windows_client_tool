"""Tests for DPC / ISR Latency & Platform Timer Investigator Engine."""
from modules.system_health.latency_engine import (
    diagnose_latency_health,
    get_qpc_clock_info,
    get_system_interrupt_stats,
    get_timer_resolution,
    sample_kernel_latency,
)


def test_get_timer_resolution():
    res = get_timer_resolution()
    assert res.min_resolution_ms > 0
    assert res.max_resolution_ms > 0
    assert res.current_resolution_ms > 0
    assert isinstance(res.is_high_resolution, bool)
    assert res.verdict != ""


def test_get_qpc_clock_info():
    clock = get_qpc_clock_info()
    assert clock.frequency_hz > 0
    assert clock.frequency_mhz > 0
    assert clock.clock_source != ""
    assert isinstance(clock.is_hpet, bool)


def test_get_system_interrupt_stats():
    stats = get_system_interrupt_stats()
    assert stats.core_count >= 1
    assert stats.distribution_note != ""


def test_sample_kernel_latency_short():
    # 0.1s sample to keep tests fast
    result = sample_kernel_latency(duration_seconds=0.1)
    assert result.samples_count > 0
    assert result.max_latency_us >= 0.0
    assert result.status in ("clean", "warning", "critical")
    assert result.verdict != ""


def test_diagnose_latency_health():
    report = diagnose_latency_health()
    assert report.timer is not None
    assert report.clock is not None
    assert report.interrupts is not None
    assert "Timer:" in report.summary

