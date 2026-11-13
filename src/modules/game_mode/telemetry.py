"""Game Mode Telemetry & Wave Metrics
====================================

Collects, rotates, and stores per-wave telemetry metrics for Game Mode sessions:
- Handles: system total process handle count
- CPU usage (%)
- GPU usage (%)
- Disk latency (ms)
- Network latency (ms)
- RAM usage (%)

Data is written to a rotating JSONLines file (`game_mode_metrics.jsonl`) and a
human-readable log (`game_mode_metrics.log`).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

METRICS_JSONL = "game_mode_metrics.jsonl"
METRICS_LOG = "game_mode_metrics.log"
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5MB rotation threshold
MAX_ROTATIONS = 3


def get_telemetry_dir(folder: Optional[str] = None) -> Path:
    if folder:
        p = Path(folder)
    else:
        app_data = os.getenv("APPDATA") or tempfile.gettempdir()
        p = Path(app_data) / "WindowsTweaker" / "game_mode"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_network_latency_ms(host: str = "8.8.8.8", port: int = 53, timeout_s: float = 0.15) -> float:
    """Measure TCP connect latency to DNS or host in milliseconds, or 0.0 if offline."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            pass
        return round((time.perf_counter() - t0) * 1000.0, 2)
    except (OSError, TimeoutError):
        return 0.0


_PREV_DISK_IO = None


def get_disk_latency_ms() -> float:
    """Estimate average disk operation latency in ms using delta I/O counters."""
    global _PREV_DISK_IO
    try:
        import psutil
        io = psutil.disk_io_counters()
        if not io:
            return 0.0
        if _PREV_DISK_IO is None:
            _PREV_DISK_IO = io
            if (io.read_count + io.write_count) > 0:
                total_ops = io.read_count + io.write_count
                total_ms = io.read_time + io.write_time
                ratio = total_ms / total_ops
                return round(ratio, 2) if ratio >= 0.01 else round(ratio, 4)
            return 0.0
        delta_ops = (io.read_count + io.write_count) - (_PREV_DISK_IO.read_count + _PREV_DISK_IO.write_count)
        delta_ms = (io.read_time + io.write_time) - (_PREV_DISK_IO.read_time + _PREV_DISK_IO.write_time)
        _PREV_DISK_IO = io
        if delta_ops > 0:
            return round(delta_ms / delta_ops, 2)
        return 0.0
    except Exception:
        pass
    return 0.0


def get_gpu_percent() -> float:
    """Read GPU utilization percentage if available, otherwise 0.0."""
    try:
        from core.procengine.gpuinfo import GpuSampler
        sampler = GpuSampler()
        try:
            usage = sampler.sample()
            # If engine utilization is available across adapters, compute highest or average
            if usage:
                max_pct = 0.0
                for a in usage:
                    if hasattr(a, "engines") and a.engines:
                        for e in a.engines:
                            pct = getattr(e, "percent", 0.0) or 0.0
                            if pct > max_pct:
                                max_pct = pct
                return round(max_pct, 1)
        finally:
            sampler.close()
    except Exception:
        pass
    return 0.0


def collect_system_telemetry(system: Any = None) -> Dict[str, Any]:
    """Capture a snapshot of handles, CPU, GPU, disk latency, and network latency."""
    out: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "handles": None,
        "processes": None,
        "cpu_percent": 0.0,
        "gpu_percent": 0.0,
        "disk_latency_ms": 0.0,
        "network_latency_ms": 0.0,
        "ram_percent": 0.0,
    }

    # 1. Handles & processes
    if system is not None and hasattr(system, "processes"):
        try:
            procs = system.processes()
            from .engine.session import total_handles
            out["handles"] = total_handles(procs)
            out["processes"] = len(procs)
        except Exception as exc:
            logger.debug("Failed getting handles from system: %s", exc)

    if out["handles"] is None:
        try:
            import psutil
            total_h = 0
            count_p = 0
            for p in psutil.process_iter(["num_handles"]):
                try:
                    h = p.info.get("num_handles")
                    if h:
                        total_h += h
                    count_p += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            out["handles"] = total_h
            out["processes"] = count_p
        except Exception as exc:
            logger.debug("Failed getting handles from psutil: %s", exc)

    # 2. CPU & RAM
    try:
        import psutil
        out["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        out["ram_percent"] = float(psutil.virtual_memory().percent)
    except Exception as exc:
        logger.debug("Failed reading CPU/RAM: %s", exc)

    # 3. GPU
    out["gpu_percent"] = get_gpu_percent()

    # 4. Latencies
    out["disk_latency_ms"] = get_disk_latency_ms()
    out["network_latency_ms"] = get_network_latency_ms()

    return out


def _rotate_if_needed(file_path: Path) -> None:
    """Rotate log files if size exceeds MAX_LOG_BYTES."""
    try:
        if not file_path.exists() or file_path.stat().st_size < MAX_LOG_BYTES:
            return
        for i in range(MAX_ROTATIONS - 1, 0, -1):
            src = file_path.with_suffix(f"{file_path.suffix}.{i}")
            dst = file_path.with_suffix(f"{file_path.suffix}.{i + 1}")
            if src.exists():
                src.replace(dst)
        file_path.replace(file_path.with_suffix(f"{file_path.suffix}.1"))
    except Exception as exc:
        logger.warning("Log rotation error for %s: %s", file_path, exc)


def record_wave_metric(wave_id: int, data: Dict[str, Any], folder: Optional[str] = None,
                       notes: str = "", **kwargs: Any) -> Dict[str, Any]:
    """Append a wave metric record to the rotating JSONLines file and text log."""
    target_dir = get_telemetry_dir(folder)
    jsonl_path = target_dir / METRICS_JSONL
    log_path = target_dir / METRICS_LOG

    _rotate_if_needed(jsonl_path)
    _rotate_if_needed(log_path)

    handles = data.get("handles")
    cpu_pct = float(data.get("cpu_percent") or 0.0)
    gpu_pct = float(data.get("gpu_percent") or 0.0)
    disk_lat = float(data.get("disk_latency_ms") or 0.0)
    net_lat = float(data.get("network_latency_ms") or 0.0)
    ram_pct = float(data.get("ram_percent") or 0.0)

    # Status evaluation (aligns with run_game_mode_waves.ps1 upper bound)
    status = data.get("status") or "ok"
    if status not in ("bad", "error") and handles is not None and handles > 350000:
        status = "warn"

    record = {
        "wave": wave_id,
        "timestamp": data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "handles": handles,
        "processes": data.get("processes"),
        "cpu_percent": round(cpu_pct, 2),
        "gpu_percent": round(gpu_pct, 2),
        "disk_latency_ms": round(disk_lat, 2),
        "network_latency_ms": round(net_lat, 2),
        "ram_percent": round(ram_pct, 2),
        "status": status,
        "notes": notes or data.get("notes", ""),
    }

    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Could not write wave metrics JSONL: %s", exc)

    try:
        h_str = f"{handles:,}" if isinstance(handles, int) else "N/A"
        summary = (
            f"[{record['timestamp']}] Wave {wave_id:02d} | Handles: {h_str:>7} | "
            f"CPU: {cpu_pct:4.1f}% | GPU: {gpu_pct:4.1f}% | Disk: {disk_lat:4.1f}ms | Net: {net_lat:4.1f}ms"
        )
        if notes:
            summary += f" | {notes}"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(summary + "\n")
    except Exception as exc:
        logger.warning("Could not write wave metrics text log: %s", exc)

    return record


def load_wave_metrics(folder: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Read the latest `limit` wave metric records from JSONLines."""
    target_dir = get_telemetry_dir(folder)
    jsonl_path = target_dir / METRICS_JSONL
    if not jsonl_path.exists():
        return []

    records: List[Dict[str, Any]] = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("Error reading wave metrics: %s", exc)

    return records[-limit:]


def clear_wave_metrics(folder: Optional[str] = None) -> None:
    """Clear metric logs for a fresh run."""
    target_dir = get_telemetry_dir(folder)
    for name in (METRICS_JSONL, METRICS_LOG):
        p = target_dir / name
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def to_log_entries(metrics: List[Dict[str, Any]]) -> List[Any]:
    """Convert raw telemetry dictionaries to core.types.LogEntry instances for LogPane."""
    from core.types import LogEntry
    entries: List[LogEntry] = []
    for m in metrics:
        ts_str = m.get("timestamp", "")
        dt = datetime.now()
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str)
            except Exception:
                pass

        wave = m.get("wave", 0)
        status = m.get("status", "ok")
        level = "Warning" if status in ("warn", "warning") else ("Error" if status in ("bad", "error") else "Info")
        handles = m.get("handles")
        h_str = f"{handles:,}" if isinstance(handles, int) else "N/A"
        cpu = m.get("cpu_percent", 0.0)
        gpu = m.get("gpu_percent", 0.0)
        disk = m.get("disk_latency_ms", 0.0)
        net = m.get("network_latency_ms", 0.0)

        msg = f"Handles: {h_str} | CPU: {cpu:.1f}% | GPU: {gpu:.1f}% | Disk: {disk:.1f}ms | Net: {net:.1f}ms"
        if m.get("notes"):
            msg += f" ({m['notes']})"

        entries.append(LogEntry(
            timestamp=dt,
            source=f"Wave {wave:02d}",
            level=level,
            message=msg,
            raw=m
        ))
    return entries


def run_wave_benchmark(
    system: Any,
    wave_count: int = 40,
    interval_s: float = 0.5,
    folder: Optional[str] = None,
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Execute consecutive test waves, capturing telemetry and cleaning up resources deterministically."""
    from core.resource_tracker import ResourceTracker
    results: List[Dict[str, Any]] = []
    tracker = ResourceTracker(base_dir=str(get_telemetry_dir(folder)))

    if log_cb:
        log_cb("info", f"Starting {wave_count}-Wave Game Mode Benchmark (interval={interval_s}s)...")

    for i in range(1, wave_count + 1):
        if cancel_check and cancel_check():
            if log_cb:
                log_cb("warn", f"Wave run cancelled at wave {i}/{wave_count}")
            break

        # Simulate or track wave resources
        tracker.track_custom(lambda: None, f"wave_{i}_marker")

        # Collect metrics
        data = collect_system_telemetry(system)
        record = record_wave_metric(i, data, folder=folder, notes="Benchmark Wave")
        results.append(record)

        # Cleanup for this wave
        tracker.cleanup(wave_id=i)

        if progress_cb:
            progress_cb(i, wave_count)

        if interval_s > 0:
            sleep_end = time.time() + interval_s
            while time.time() < sleep_end:
                if cancel_check and cancel_check():
                    break
                time.sleep(min(0.05, max(0.0, sleep_end - time.time())))

    if log_cb:
        log_cb("ok", f"Completed {len(results)}/{wave_count} waves successfully.")

    return results
