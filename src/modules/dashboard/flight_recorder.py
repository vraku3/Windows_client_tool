"""Flight Recorder: capture the machine's vital signs over time, save them, and
scrub back through them later. No Qt.

The value is being able to look at what happened at 03:12 -- the spike that was
gone by the time anyone looked. A `.trace` file is JSON Lines: one header line,
then one sample per line, so a recording that was cut off (crash, power loss)
still loads up to its last complete line.

Version 2 (2026-10-09), after TMOG Pro's Flight Recorder and AppControl's
"historical Task Manager":

- eight panels: CPU, memory, commit, disk, network, effective clock, GPU and
  CPU package power. A reading that could not be taken is stored as None and
  drawn as a GAP, never as 0 -- version 1 wrote `mhz or 0.0`, so a refused
  clock counter recorded "0 MHz";
- per sample, WHO: the top processes by CPU, memory, GPU and disk I/O, so the
  question at 03:12 is "which app", not just "how much";
- "new" events: a program appearing for the first time in the recording;
- `at`, the wall-clock time, so a day-long history can be read by the clock;
- a Recorder given a path writes each sample as it is taken -- version 1 kept
  everything in memory until Stop, so a crash lost the whole recording that
  the JSON Lines format was chosen to survive.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

FORMAT = "wct-trace"
VERSION = 2
MAX_SAMPLES = 20_000        # 5.5 h at 1 s; recording stops rather than silently dropping the start

PANELS: Tuple[Tuple[str, str, str], ...] = (
    ("cpu", "CPU", "%"),
    ("mem", "Memory", "%"),
    ("commit", "Commit", "%"),
    ("disk", "Disk", "MB/s"),
    ("net", "Network", "MB/s"),
    ("mhz", "Clock", "MHz"),
    ("gpu", "GPU", "%"),
    ("power", "CPU package", "W"),
)

#: How many processes each per-sample ranking keeps.
TOP_N = {"cpu": 5, "mem": 5, "gpu": 3, "io": 3}

#: The first samples only establish what was already running; a program is
#: "new" when it appears after them.
BASELINE_SAMPLES = 2


class ProcRow(NamedTuple):
    """One process, as a Sampler's `processes` provider returns it."""
    name: str
    pid: int
    cpu: Optional[float]        # percent of the whole machine
    mem: Optional[int]          # private working set, bytes
    io: Optional[float]         # read + write + other, bytes/s


@dataclass
class Trace:
    interval: float = 1.0
    started: str = ""
    machine: str = ""
    samples: List[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.samples[-1]["t"] if self.samples else 0.0

    def series(self, key: str) -> List[Optional[float]]:
        """One panel's values; None where the reading was not taken (a gap)."""
        return [s.get(key) for s in self.samples]

    def wall_clock(self, index: int) -> Optional[datetime]:
        s = self.samples[index]
        if s.get("at"):
            return datetime.fromtimestamp(s["at"])
        try:
            return datetime.fromisoformat(self.started) + timedelta(seconds=s["t"])
        except ValueError:
            return None

    def top_at(self, index: int) -> Dict[str, List[list]]:
        """{"cpu": [[name, pid, value], ...], "mem": ..., "gpu": ..., "io": ...}.
        A version 1 sample only knew its single busiest process."""
        s = self.samples[index]
        if "procs" in s:
            return {k: list(v) for k, v in s["procs"].items()}
        return {"cpu": [[s["top"], 0, s.get("top_cpu")]]} if s.get("top") else {}

    def events(self) -> List[Tuple[int, str]]:
        """(sample index, program) for every program that first appeared mid-recording."""
        return [(i, name) for i, s in enumerate(self.samples) for name in s.get("new", ())]

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
        vals = [(i, v) for i, v in enumerate(self.series(key)) if v is not None]
        if not vals:
            return None
        return max(vals, key=lambda iv: iv[1])

    def describe(self, index: int) -> str:
        s = self.samples[index]
        clock = self.wall_clock(index)
        when = f"{clock:%Y-%m-%d %H:%M:%S}" if clock else ""
        when += f"  (+{int(s['t'] // 60)}m{int(s['t'] % 60):02d}s)"

        def val(key: str, fmt: str) -> str:
            v = s.get(key)
            return "n/a" if v is None else format(v, fmt)
        top = self.top_at(index).get("cpu") or []
        busiest = f"{top[0][0]} ({top[0][2] or 0:.0f}%)" if top else "?"
        return (f"{when}   CPU {val('cpu', '.0f')}%   RAM {val('mem', '.0f')}%   "
                f"commit {val('commit', '.0f')}%   disk {val('disk', '.1f')} MB/s   "
                f"net {val('net', '.2f')} MB/s   {val('mhz', ',.0f')} MHz   "
                f"GPU {val('gpu', '.0f')}%   {val('power', '.0f')} W   top: {busiest}")


def _ranked(rows: List[ProcRow], value: Callable[[ProcRow], Optional[float]], n: int,
            scale: float = 1.0, digits: int = 1) -> List[list]:
    scored = [(r, value(r)) for r in rows if r.pid != 0]
    scored = [(r, v) for r, v in scored if v]
    scored.sort(key=lambda rv: rv[1], reverse=True)
    return [[r.name, r.pid, round(v / scale, digits)] for r, v in scored[:n]]


def rank_processes(rows: List[ProcRow], gpu_by_pid: Optional[Dict[int, float]] = None) -> Dict[str, list]:
    """The per-sample "who": top N by CPU %, memory MB, GPU % and disk MB/s.
    A value that was not measured is left out, never ranked as zero."""
    out = {"cpu": _ranked(rows, lambda r: r.cpu, TOP_N["cpu"]),
           "mem": _ranked(rows, lambda r: r.mem, TOP_N["mem"], scale=1048576, digits=0),
           "io": _ranked(rows, lambda r: r.io, TOP_N["io"], scale=1048576, digits=2)}
    if gpu_by_pid is not None:
        out["gpu"] = _ranked(rows, lambda r: gpu_by_pid.get(r.pid), TOP_N["gpu"])
    return out


class Sampler:
    """Takes one sample per call. Rates are deltas, so the first sample has
    none and is skipped by the recorder.

    Every provider is optional and may return None, which is recorded as None
    (a gap). `processes` returns every process as `ProcRow`s; `gpu_by_pid`
    returns {pid: percent} or None when the GPU counters are unavailable.
    """

    def __init__(self, top_process: Optional[Callable[[], Tuple[str, float]]] = None,
                 clock: Optional[Callable[[], Optional[float]]] = None,
                 processes: Optional[Callable[[], Optional[List[ProcRow]]]] = None,
                 gpu: Optional[Callable[[], Optional[float]]] = None,
                 gpu_by_pid: Optional[Callable[[], Optional[Dict[int, float]]]] = None,
                 power: Optional[Callable[[], Optional[float]]] = None) -> None:
        import psutil
        self._psutil = psutil
        self._top = top_process
        self._clock, self._processes, self._gpu = clock, processes, gpu
        self._gpu_by_pid, self._power = gpu_by_pid, power
        self._last = None
        self._seen: set = set()
        self._calls = 0
        psutil.cpu_percent(interval=None)

    def sample(self) -> Optional[dict]:
        ps = self._psutil
        now = time.monotonic()
        disk, net = ps.disk_io_counters(), ps.net_io_counters()
        prev, self._last = self._last, (now, disk, net)
        rows = self._processes() if self._processes else None
        new = self._new_programs(rows)
        if prev is None:
            return None
        dt = max(now - prev[0], 1e-6)
        vm, sw = ps.virtual_memory(), ps.swap_memory()
        sample = {
            "at": round(time.time(), 1),
            "cpu": ps.cpu_percent(interval=None),
            "mem": vm.percent, "commit": sw.percent,
            "disk": ((disk.read_bytes - prev[1].read_bytes) + (disk.write_bytes - prev[1].write_bytes)) / dt / 1048576,
            "net": ((net.bytes_recv - prev[2].bytes_recv) + (net.bytes_sent - prev[2].bytes_sent)) / dt / 1048576,
            "mhz": self._clock() if self._clock else None,
            "gpu": self._gpu() if self._gpu else None,
            "power": self._power() if self._power else None,
        }
        self._add_who(sample, rows)
        if new:
            sample["new"] = new
        return sample

    def _add_who(self, sample: dict, rows: Optional[List[ProcRow]]) -> None:
        if rows is not None:
            sample["procs"] = rank_processes(rows, self._gpu_by_pid() if self._gpu_by_pid else None)
            top = sample["procs"]["cpu"]
            sample["top"], sample["top_cpu"] = (top[0][0], top[0][2]) if top else ("", 0.0)
        elif self._top:
            sample["top"], sample["top_cpu"] = self._top()

    def _new_programs(self, rows: Optional[List[ProcRow]]) -> List[str]:
        """Programs never seen before in this recording, once the baseline is set.
        By NAME, not PID: a browser's tenth tab process is not a new app."""
        if rows is None:
            return []
        self._calls += 1
        names = {r.name.lower(): r.name for r in rows if r.name}
        fresh = [] if self._calls <= BASELINE_SAMPLES else \
            sorted(names[k] for k in names.keys() - self._seen)
        self._seen.update(names)
        return fresh


class Recorder:
    """Stamps samples with `t`. Given a `path`, also appends each one to that
    file as it arrives (header first), so a crash keeps everything up to the
    last full line."""

    def __init__(self, interval: float = 1.0, machine: str = "", path: Optional[str] = None,
                 max_samples: Optional[int] = None) -> None:
        self.trace = Trace(interval=interval, started=datetime.now().isoformat(timespec="seconds"),
                           machine=machine)
        self._t0 = time.monotonic()
        self.full = False
        self.path = path
        self._max = max_samples
        self._handle = None
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._handle = open(path, "w", encoding="utf-8")
            self._handle.write(json.dumps(_header(self.trace)) + "\n")
            self._handle.flush()

    def add(self, sample: Optional[dict]) -> bool:
        """Append a sample. Returns False once the recording is full."""
        if sample is None:
            return True
        if len(self.trace.samples) >= (self._max or MAX_SAMPLES):
            self.full = True
            return False
        sample = dict(sample)
        sample["t"] = round(time.monotonic() - self._t0, 3)
        self.trace.samples.append(sample)
        if self._handle is not None:
            try:
                self._handle.write(json.dumps(sample, separators=(",", ":")) + "\n")
                self._handle.flush()
            except OSError as e:     # a full disk must not stop the in-memory recording
                logger.warning("could not append to %s: %s", self.path, e)
        return True

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError as e:
                logger.warning("could not close %s: %s", self.path, e)
            self._handle = None


# ---- files ---------------------------------------------------------------------------

def _header(trace: Trace) -> dict:
    return {"format": FORMAT, "version": VERSION, "interval": trace.interval,
            "started": trace.started, "machine": trace.machine}


def save(trace: Trace, path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_header(trace)) + "\n")
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


# ---- rolling history (AppControl-style "scroll back through days") ----------------------

HISTORY_INTERVAL = 5.0
HISTORY_KEEP_DAYS = 7
#: One day at 5 s is 17,280 samples; a file is rolled at midnight anyway.
HISTORY_MAX_SAMPLES = 20_000
_HISTORY_PREFIX = "history-"


def history_path(directory: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return os.path.join(directory, now.strftime(f"{_HISTORY_PREFIX}%Y%m%d-%H%M%S.trace"))


def _history_date(name: str) -> Optional[datetime]:
    if not (name.startswith(_HISTORY_PREFIX) and name.endswith(".trace")):
        return None
    try:
        return datetime.strptime(name[len(_HISTORY_PREFIX):len(_HISTORY_PREFIX) + 8], "%Y%m%d")
    except ValueError:
        return None


def prune_history(directory: str, keep_days: int = HISTORY_KEEP_DAYS,
                  now: Optional[datetime] = None) -> List[str]:
    """Delete history files dated more than `keep_days` ago; returns what was
    deleted. Only files this module named are ever touched -- a recording the
    user saved under any other name is never pruned."""
    if not os.path.isdir(directory):
        return []
    cutoff = (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0) \
        - timedelta(days=keep_days - 1)
    removed = []
    for name in sorted(os.listdir(directory)):
        day = _history_date(name)
        if day is None or day >= cutoff:
            continue
        path = os.path.join(directory, name)
        try:
            os.remove(path)
            removed.append(path)
        except OSError as e:
            logger.warning("could not prune old history %s: %s", path, e)
    return removed


class RollingHistory:
    """An always-on Recorder that starts a new file at midnight or when full."""

    def __init__(self, directory: str, machine: str = "", interval: float = HISTORY_INTERVAL,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.directory, self.machine, self.interval = directory, machine, interval
        self._now = clock
        self._recorder: Optional[Recorder] = None
        self._day = None

    @property
    def path(self) -> Optional[str]:
        return self._recorder.path if self._recorder else None

    def _roll(self) -> None:
        self.close()
        now = self._now()
        self._recorder = Recorder(self.interval, self.machine, history_path(self.directory, now),
                                  max_samples=HISTORY_MAX_SAMPLES)
        self._day = now.date()
        prune_history(self.directory, now=now)

    def add(self, sample: Optional[dict]) -> None:
        if sample is None:
            return
        if self._recorder is None or self._now().date() != self._day or self._recorder.full:
            self._roll()
        if not self._recorder.add(sample):
            self._roll()
            self._recorder.add(sample)

    def close(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None


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
