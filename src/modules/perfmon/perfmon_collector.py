import csv
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger(__name__)

#: Counters `PerfMonStore` holds that are plain 0-100 percentages, so they can
#: be replayed straight onto a chart with no rate/derivative math. `net_*` and
#: `disk_*_bytes` are cumulative counters (see `collect_snapshot`) -- charting
#: those verbatim would show an ever-climbing line, not the KB/s rate the
#: live Network tab computes from two consecutive readings. History replay is
#: scoped to the counters that are already meaningful as single values.
REPLAYABLE_COUNTERS: Dict[str, str] = {
    "cpu_total": "CPU %",
    "memory_percent": "Memory %",
    "disk_percent": "Disk %",
}


def collect_snapshot() -> Dict[str, float]:
    """Collect a single snapshot of performance counters."""
    mem = psutil.virtual_memory()
    system_drive = os.environ.get("SystemDrive", "C:") + "/"
    disk = psutil.disk_usage(system_drive)
    net = psutil.net_io_counters()
    dio = psutil.disk_io_counters()
    return {
        "cpu_total": psutil.cpu_percent(interval=None),
        "memory_percent": mem.percent,
        "memory_used_mb": mem.used / (1024 * 1024),
        "memory_available_mb": mem.available / (1024 * 1024),
        "disk_percent": disk.percent,
        "disk_read_bytes": dio.read_bytes if dio else 0,
        "disk_write_bytes": dio.write_bytes if dio else 0,
        "net_sent_bytes": net.bytes_sent,
        "net_recv_bytes": net.bytes_recv,
    }


class PerfMonStore:
    """SQLite-backed storage for historical performance data."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS perfmon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                counter TEXT NOT NULL,
                value REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_perfmon_ts ON perfmon(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_perfmon_counter ON perfmon(counter)
        """)
        self._conn.commit()

    def store_snapshot(self, snapshot: Dict[str, float]) -> None:
        """Store a performance snapshot."""
        if not self._conn:
            return
        ts = datetime.now().isoformat()
        rows = [(ts, counter, value) for counter, value in snapshot.items()]
        self._conn.executemany(
            "INSERT INTO perfmon (timestamp, counter, value) VALUES (?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def query(self, counter: str, hours_back: int = 1) -> List[tuple]:
        """Return (timestamp_str, value) tuples for a counter."""
        if not self._conn:
            return []
        cutoff = (datetime.now() - timedelta(hours=hours_back)).isoformat()
        cursor = self._conn.execute(
            "SELECT timestamp, value FROM perfmon WHERE counter = ? AND timestamp > ? ORDER BY timestamp",
            (counter, cutoff),
        )
        return cursor.fetchall()

    def cleanup_old(self, days: int = 7) -> None:
        """Delete records at or older than N days.

        `<=`, not `<`. With `days=0` the cutoff is this instant, and a sample
        stored in the same clock tick carried exactly that timestamp and
        survived a call whose whole point was to delete everything. At the
        default of 7 days the difference is one microsecond and nothing else.
        """
        if not self._conn:
            return
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        self._conn.execute("DELETE FROM perfmon WHERE timestamp <= ?", (cutoff,))
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


def downsample(rows: List[Tuple[str, float]], max_points: int = 300) -> List[Tuple[str, float]]:
    """Thin `rows` (as returned by `PerfMonStore.query`) to at most
    `max_points` for chart display.

    `PerfMonStore` writes one sample per counter per minute, so a 7-day
    `query()` returns up to 10,080 rows. Feeding all of them into
    `_QtLineChart.set_series` is a real cost, not a theoretical one: every
    point recomputes its own (x, y) in `_to_xy`, and the axis writes an
    OS-level DPI-scaled `drawText` per gridline on every repaint. This keeps
    the point count fixed regardless of range, and always keeps the very
    first and last sample so the endpoints of the requested window are never
    silently trimmed off.
    """
    n = len(rows)
    if n <= max_points or max_points <= 1:
        return rows
    step = (n - 1) / (max_points - 1)
    indices = {round(i * step) for i in range(max_points)}
    return [rows[i] for i in sorted(indices)]


def compute_summary(values: List[float]) -> Optional[Dict[str, float]]:
    """Min/avg/max/count over a history window -- the "how bad did it get"
    question the History tab's charts don't answer at a glance: reading a
    peak off a 300-point downsampled line is a guess, this is the exact
    number from every raw sample in the window. `None` for an empty window,
    never a 0/0/0 that would read as a perfectly idle machine when the real
    answer is "nothing recorded yet".
    """
    if not values:
        return None
    return {
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
        "count": float(len(values)),
    }


def write_history_csv(rows_by_counter: Dict[str, List[Tuple[str, float]]], path: str) -> int:
    """Write `rows_by_counter` (as returned by repeated `PerfMonStore.query`
    calls, one per `REPLAYABLE_COUNTERS` key) as a single wide-format CSV --
    one row per sample timestamp, one column per counter -- so the history
    this app already collects can be handed to another engineer or opened in
    Excel, which a SQLite file cannot do on its own.

    Merges rows across counters by an EXACT timestamp string match, which is
    safe here specifically because `PerfMonStore.store_snapshot` writes every
    counter from one snapshot in a single `executemany` call sharing one
    `datetime.now().isoformat()` string -- samples from the same collection
    tick line up exactly. A counter missing a value for a given timestamp
    (e.g. a counter added to `REPLAYABLE_COUNTERS` after the others were
    already running) is written as an empty cell, never a fabricated 0.

    Returns the number of timestamp rows written (0 for an empty range --
    the file still gets a header, it is never left unwritten).
    """
    counters = list(rows_by_counter.keys())
    merged: Dict[str, Dict[str, float]] = {}
    for counter, rows in rows_by_counter.items():
        for ts, value in rows:
            merged.setdefault(ts, {})[counter] = value
    timestamps = sorted(merged.keys())

    header = ["timestamp"] + [REPLAYABLE_COUNTERS.get(c, c) for c in counters]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for ts in timestamps:
            row_values = merged[ts]
            writer.writerow(
                [ts] + [
                    "" if counter not in row_values else f"{row_values[counter]:.2f}"
                    for counter in counters
                ]
            )
    return len(timestamps)
