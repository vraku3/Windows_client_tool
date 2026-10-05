"""Flight Recorder: capture the machine's vital signs over time, save them, and
scrub back through them later. No Qt.

The value is being able to look at what happened at 03:12 -- the spike that was
gone by the time anyone looked. A `.trace` file is JSON Lines: one header line,
then one sample per line, so a recording that was cut off (crash, power loss)
still loads up to its last complete line.

Seven panels per sample: CPU, memory, commit, disk throughput, network
throughput, effective clock, and the busiest process at that instant.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FORMAT = "wct-trace"
VERSION = 1
MAX_SAMPLES = 20_000        # 5.5 h at 1 s; recording stops rather than silently dropping the start

PANELS: Tuple[Tuple[str, str, str], ...] = (
    ("cpu", "CPU", "%"),
    ("mem", "Memory", "%"),
    ("commit", "Commit", "%"),
    ("disk", "Disk", "MB/s"),
    ("net", "Network", "MB/s"),
    ("mhz", "Clock", "MHz"),
)


@dataclass
class Trace:
    interval: float = 1.0
    started: str = ""
    machine: str = ""
    samples: List[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.samples[-1]["t"] if self.samples else 0.0

    def series(self, key: str) -> List[float]:
        return [s.get(key) or 0.0 for s in self.samples]

    def nearest(self, t: float) -> Optional[int]:
        """Index of the sample closest to time `t` (seconds from the start)."""
        if not self.samples:
            return None
        lo, hi = 0, len(self.samples) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.samples[mid]["t"] < t:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0 and abs(self.samples[lo - 1]["t"] - t) <= abs(self.samples[lo]["t"] - t):
            lo -= 1
        return lo

    def peak(self, key: str) -> Optional[Tuple[int, float]]:
        vals = self.series(key)
        if not vals:
            return None
        i = max(range(len(vals)), key=vals.__getitem__)
        return i, vals[i]

    def describe(self, index: int) -> str:
        s = self.samples[index]
        when = f"+{int(s['t'] // 60)}m{int(s['t'] % 60):02d}s"
        return (f"{when}   CPU {s.get('cpu', 0):.0f}%   RAM {s.get('mem', 0):.0f}%   "
                f"commit {s.get('commit', 0):.0f}%   disk {s.get('disk', 0):.1f} MB/s   "
                f"net {s.get('net', 0):.2f} MB/s   {s.get('mhz', 0):,.0f} MHz   "
                f"top: {s.get('top', '?')} ({s.get('top_cpu', 0):.0f}%)")


class Sampler:
    """Takes one sample per call. Rates are deltas, so the first sample has
    none and is skipped by the recorder."""

    def __init__(self, top_process: Optional[Callable[[], Tuple[str, float]]] = None,
                 clock: Optional[Callable[[], Optional[float]]] = None) -> None:
        import psutil
        self._psutil = psutil
        self._top = top_process
        self._clock = clock
        self._last = None
        psutil.cpu_percent(interval=None)

    def sample(self) -> Optional[dict]:
        ps = self._psutil
        now = time.monotonic()
        disk, net = ps.disk_io_counters(), ps.net_io_counters()
        prev, self._last = self._last, (now, disk, net)
        if prev is None:
            return None
        dt = max(now - prev[0], 1e-6)
        vm, sw = ps.virtual_memory(), ps.swap_memory()
        name, top_cpu = self._top() if self._top else ("", 0.0)
        mhz = self._clock() if self._clock else None
        return {
            "cpu": ps.cpu_percent(interval=None),
            "mem": vm.percent, "commit": sw.percent,
            "disk": ((disk.read_bytes - prev[1].read_bytes) + (disk.write_bytes - prev[1].write_bytes)) / dt / 1048576,
            "net": ((net.bytes_recv - prev[2].bytes_recv) + (net.bytes_sent - prev[2].bytes_sent)) / dt / 1048576,
            "mhz": mhz or 0.0, "top": name, "top_cpu": top_cpu,
        }


class Recorder:
    def __init__(self, interval: float = 1.0, machine: str = "") -> None:
        self.trace = Trace(interval=interval, started=datetime.now().isoformat(timespec="seconds"),
                           machine=machine)
        self._t0 = time.monotonic()
        self.full = False

    def add(self, sample: Optional[dict]) -> bool:
        """Append a sample. Returns False once the recording is full."""
        if sample is None:
            return True
        if len(self.trace.samples) >= MAX_SAMPLES:
            self.full = True
            return False
        sample = dict(sample)
        sample["t"] = round(time.monotonic() - self._t0, 3)
        self.trace.samples.append(sample)
        return True


# ---- files ---------------------------------------------------------------------------

def save(trace: Trace, path: str) -> None:
    header = {"format": FORMAT, "version": VERSION, "interval": trace.interval,
              "started": trace.started, "machine": trace.machine}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        for s in trace.samples:
            handle.write(json.dumps(s, separators=(",", ":")) + "\n")
    os.replace(tmp, path)          # never leave a half-written .trace under the real name


def load(path: str) -> Trace:
    """Load a trace. A truncated final line is dropped; a non-trace file raises."""
    with open(path, encoding="utf-8") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except ValueError as e:
            raise ValueError("not a trace file") from e
        if header.get("format") != FORMAT:
            raise ValueError("not a trace file")
        if header.get("version", 0) > VERSION:
            raise ValueError(f"trace version {header.get('version')} is newer than this app understands")
        trace = Trace(interval=header.get("interval", 1.0), started=header.get("started", ""),
                      machine=header.get("machine", ""))
        skipped = 0
        for line in handle:
            try:
                sample = json.loads(line)
                if isinstance(sample, dict) and "t" in sample:
                    trace.samples.append(sample)
                else:
                    skipped += 1
            except ValueError:
                skipped += 1
        if skipped:
            logger.warning("%s: %d unreadable line(s) skipped", path, skipped)
    return trace


def default_path(directory: str) -> str:
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, datetime.now().strftime("recording-%Y%m%d-%H%M%S.trace"))


@dataclass(frozen=True)
class SavedTrace:
    """One `.trace` file's metadata, read without loading every sample into memory."""
    path: str
    name: str
    started: str
    machine: str
    interval: float
    size_bytes: int
    modified: float
    duration: Optional[float]      # seconds; None if no sample could be read
    sample_count: Optional[int]    # None alongside duration, for the same reason
    readable: bool                 # False: the header itself could not be parsed


def _scan_samples(path: str) -> Tuple[int, Optional[float]]:
    """(count, duration) for every sample line after the header, tolerating a
    torn final line the same way `load()` does -- skip it, do not raise."""
    count = 0
    duration = None
    skipped = 0
    with open(path, encoding="utf-8") as handle:
        handle.readline()      # header; already validated by the caller
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(sample, dict) and "t" in sample:
                count += 1
                duration = sample["t"]
            else:
                skipped += 1
    if skipped:
        logger.warning("%s: %d unreadable sample line(s) skipped", path, skipped)
    return count, duration


def list_saved_traces(directory: str) -> List[SavedTrace]:
    """Every `.trace` file in `directory`, newest-modified first.

    A file whose header cannot be parsed is still listed, with `readable=False`
    and `duration=None` -- hiding it would make a saved recording silently
    disappear from the library rather than showing it as unreadable.
    """
    if not os.path.isdir(directory):
        return []
    found: List[SavedTrace] = []
    for name in os.listdir(directory):
        if not name.endswith(".trace"):
            continue
        path = os.path.join(directory, name)
        try:
            stat = os.stat(path)
        except OSError as e:
            logger.warning("could not stat %s: %s", path, e)
            continue
        started = machine = ""
        interval = 1.0
        readable = True
        try:
            with open(path, encoding="utf-8") as handle:
                header = json.loads(handle.readline())
            if header.get("format") != FORMAT:
                raise ValueError("not a trace file")
            started = header.get("started", "")
            machine = header.get("machine", "")
            interval = header.get("interval", 1.0)
        except (OSError, ValueError) as e:
            logger.warning("%s: header unreadable, listing it anyway: %s", path, e)
            readable = False
        count = duration = None
        if readable:
            try:
                count, duration = _scan_samples(path)
            except OSError as e:
                logger.warning("%s: could not scan its samples: %s", path, e)
        found.append(SavedTrace(path=path, name=name, started=started, machine=machine,
                                interval=interval, size_bytes=stat.st_size, modified=stat.st_mtime,
                                duration=duration, sample_count=count, readable=readable))
    found.sort(key=lambda t: t.modified, reverse=True)
    return found
