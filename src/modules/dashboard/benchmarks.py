"""Short, cancellable microbenchmarks: CPU, memory and disk. No Qt.

These are for comparing THIS machine with itself (before and after a driver, a
BIOS setting, a power plan) and for a rough sanity check against what the
hardware should do -- not a substitute for a published benchmark suite, and the
tab says so. Every run is saved with its timestamp so two runs can be compared.

Design rules:
- Each test is short (a few seconds) and checks `cancelled()` between chunks.
- Nothing is estimated: a test that cannot run reports why, it does not guess.
- The disk test writes a temporary file in the chosen folder, flushes it to the
  device, reads it back UNBUFFERED (so the number is the disk, not the cache),
  and deletes it in a `finally`.
"""
import ctypes
import hashlib
import json
import logging
import mmap
import os
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

HISTORY_KEEP = 30


@dataclass
class BenchResult:
    name: str
    value: float
    unit: str
    detail: str = ""
    note: str = ""

    def text(self) -> str:
        return f"{self.value:,.1f} {self.unit}"


class Cancelled(Exception):
    pass


def _check(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise Cancelled()


# ---- CPU -------------------------------------------------------------------------------

def _hash_loop(seconds: float, cancelled, out: list) -> None:
    """SHA-256 over 1 MiB blocks; hashlib drops the GIL for big buffers, so
    threads of this genuinely run on separate cores."""
    block = os.urandom(1 << 20)
    done = 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end and not cancelled():
        hashlib.sha256(block).digest()
        done += 1
    out.append(done)


def cpu_single(cancelled=lambda: False, seconds: float = 2.0) -> BenchResult:
    out: list = []
    start = time.perf_counter()
    _hash_loop(seconds, cancelled, out)
    _check(cancelled)
    rate = out[0] * (1 << 20) / (time.perf_counter() - start) / (1 << 20)
    return BenchResult("CPU single-thread", rate, "MB/s", "SHA-256 over 1 MiB blocks, one thread")


def cpu_multi(cancelled=lambda: False, seconds: float = 2.0,
              threads: Optional[int] = None) -> BenchResult:
    n = threads or (os.cpu_count() or 1)
    out: list = []
    workers = [threading.Thread(target=_hash_loop, args=(seconds, cancelled, out)) for _ in range(n)]
    start = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    _check(cancelled)
    rate = sum(out) * (1 << 20) / (time.perf_counter() - start) / (1 << 20)
    return BenchResult("CPU multi-thread", rate, "MB/s", f"SHA-256, {n} threads")


# ---- memory -----------------------------------------------------------------------------

def memory_bandwidth(cancelled=lambda: False, size_mb: int = 128, passes: int = 6) -> BenchResult:
    """Copy bandwidth: a buffer much bigger than any cache, copied repeatedly."""
    src = bytearray(size_mb << 20)
    dst = bytearray(size_mb << 20)
    src[::4096] = b"\x01" * len(src[::4096])          # touch every page so it is really committed
    dst[::4096] = b"\x01" * len(dst[::4096])
    view_s, view_d = memoryview(src), memoryview(dst)
    best = 0.0
    for _ in range(passes):
        _check(cancelled)
        start = time.perf_counter()
        view_d[:] = view_s
        elapsed = time.perf_counter() - start
        best = max(best, (size_mb / 1024) / elapsed)
    return BenchResult("Memory copy", best * 1024, "MB/s",
                       f"best of {passes} copies of {size_mb} MB",
                       "Copy bandwidth counts each byte once, so it reads below the "
                       "read+write figure vendors quote.")


# ---- disk -------------------------------------------------------------------------------

_GENERIC_READ, _OPEN_EXISTING = 0x80000000, 3
_FILE_FLAG_NO_BUFFERING, _FILE_FLAG_SEQUENTIAL_SCAN = 0x20000000, 0x08000000


def _unbuffered_read_mbps(path: str, block: int, cancelled) -> float:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    handle = kernel32.CreateFileW(path, _GENERIC_READ, 1, None, _OPEN_EXISTING,
                                  _FILE_FLAG_NO_BUFFERING | _FILE_FLAG_SEQUENTIAL_SCAN, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(f"could not open the test file unbuffered (error {ctypes.get_last_error()})")
    buffer = mmap.mmap(-1, block)              # page-aligned, as NO_BUFFERING requires
    address = ctypes.addressof(ctypes.c_char.from_buffer(buffer))
    total = 0
    read = wintypes.DWORD()
    start = time.perf_counter()
    try:
        while True:
            _check(cancelled)
            if not kernel32.ReadFile(handle, address, block, ctypes.byref(read), None) or read.value == 0:
                break
            total += read.value
    finally:
        kernel32.CloseHandle(handle)
        buffer.close()
    return total / (1 << 20) / (time.perf_counter() - start)


def disk_sequential(folder: str, cancelled=lambda: False, size_mb: int = 256) -> List[BenchResult]:
    """Sequential write (flushed to the device) and unbuffered read."""
    block = 1 << 20
    payload = os.urandom(block)                # incompressible, so a compressing SSD cannot cheat
    fd, path = tempfile.mkstemp(prefix="wct_bench_", dir=folder)
    try:
        start = time.perf_counter()
        with os.fdopen(fd, "wb", buffering=0) as handle:
            for _ in range(size_mb):
                _check(cancelled)
                handle.write(payload)
            os.fsync(handle.fileno())
        write = size_mb / (time.perf_counter() - start)
        read = _unbuffered_read_mbps(path, block, cancelled)
    finally:
        try:
            os.remove(path)
        except OSError as e:
            logger.warning("could not remove benchmark file %s: %s", path, e)
    where = os.path.splitdrive(os.path.abspath(folder))[0] or folder
    note = "Write is flushed to the device; read bypasses the cache."
    return [BenchResult(f"Disk write ({where})", write, "MB/s", f"{size_mb} MB sequential, 1 MiB blocks", note),
            BenchResult(f"Disk read ({where})", read, "MB/s", f"{size_mb} MB sequential, unbuffered", note)]


# ---- run + history ----------------------------------------------------------------------

def run_all(folder: str, cancelled=lambda: False, on_step: Callable[[str], None] = lambda s: None,
            quick: bool = False) -> List[BenchResult]:
    seconds = 1.0 if quick else 2.0
    size = 32 if quick else 256
    steps = [
        ("CPU single-thread", lambda: [cpu_single(cancelled, seconds)]),
        ("CPU multi-thread", lambda: [cpu_multi(cancelled, seconds)]),
        ("Memory", lambda: [memory_bandwidth(cancelled, 32 if quick else 128, 3 if quick else 6)]),
        ("Disk", lambda: disk_sequential(folder, cancelled, size)),
    ]
    results: List[BenchResult] = []
    for label, step in steps:
        if cancelled():
            break
        on_step(label)
        try:
            results += step()
        except Cancelled:
            logger.info("benchmarks cancelled during %s", label)
            break
        except OSError as e:
            logger.warning("benchmark %s failed: %s", label, e)
            results.append(BenchResult(label, 0.0, "", f"could not run: {e}"))
    return results


def save_run(directory: str, results: List[BenchResult], machine: str = "") -> str:
    """Append this run to the history file (newest last, capped) and return its path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "benchmarks.json")
    history = load_history(directory)
    history.append({"when": datetime.now().isoformat(timespec="seconds"), "machine": machine,
                    "results": [asdict(r) for r in results]})
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(history[-HISTORY_KEEP:], handle, indent=1)
    return path


def load_history(directory: str) -> list:
    path = os.path.join(directory, "benchmarks.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        logger.warning("benchmark history unreadable (%s): %s", path, e)
        return []


def compare(previous: list, current: List[BenchResult]) -> dict:
    """{name: percent change vs the most recent earlier run of the same test}."""
    last = {}
    for run in previous:
        for r in run.get("results", []):
            if r.get("value"):
                last[r["name"]] = r["value"]
    return {r.name: (r.value - last[r.name]) * 100.0 / last[r.name]
            for r in current if r.name in last and last[r.name] and r.value}


def trend(history: list, min_runs: int = 3) -> Dict[str, dict]:
    """Per-test name -> {"values": [...], "pct_change": float} for a test whose
    value DROPPED in every one of the last `min_runs` consecutive saved runs.

    `compare()` above answers "vs the one run before this" -- noisy by itself:
    a single slow run (another process using the disk, a thermal blip) reads
    as a regression and a recovered one reads as an improvement, both wrongly.
    Requiring a STRICT run-over-run decline across `min_runs` transitions
    (so `min_runs + 1` data points) is what tells a one-off dip apart from an
    SSD actually wearing out or a driver regression that holds -- it only
    fires on a run of bad luck that keeps not getting better.

    For every test here, more is better (MB/s), so "declined" always means
    the same direction; no per-test sign table is needed.
    """
    series: Dict[str, List[float]] = {}
    for run in history:
        for r in run.get("results", []):
            value = r.get("value")
            if value:
                series.setdefault(r["name"], []).append(value)
    out: Dict[str, dict] = {}
    for name, values in series.items():
        if len(values) < min_runs + 1:
            continue
        window = values[-(min_runs + 1):]
        if all(window[i + 1] < window[i] for i in range(len(window) - 1)):
            out[name] = {"values": window,
                        "pct_change": (window[-1] - window[0]) * 100.0 / window[0]}
    return out
