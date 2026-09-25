"""Per-process network throughput, from the kernel's network ETW provider. No Qt.

Windows exposes bytes-per-adapter as counters but bytes-per-PROCESS only through
an event trace: `Microsoft-Windows-Kernel-Network` emits one event per TCP/UDP
send and receive with the owning PID and the size. This consumes those events
and keeps running totals per PID.

Constraints, all of which are reported to the user and none swallowed:
- Starting a trace session needs administrator rights. Unelevated, `start()`
  answers (False, reason) and nothing is faked.
- A trace session lives in the kernel, not in this process. If the app died
  without stopping it the session would keep running, so `start()` first stops
  any stale session of our own name, `stop()` is registered with `atexit`, and
  the session is stopped by name (not by handle) so a restart can always clean up.
- Counting is off the UI thread and lock-protected; the callback does the
  minimum (parse two integers, add).
"""
import atexit
import logging
import threading
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SESSION_NAME = "WinClientTool_NetTrace"
PROVIDER_GUID = "{7DD42A49-5329-4832-8DFD-43D979153A88}"      # Microsoft-Windows-Kernel-Network

#: TCP/UDP over IPv4/IPv6, from the provider's manifest.
SEND_IDS = frozenset({10, 26, 42, 58})
RECV_IDS = frozenset({11, 27, 43, 59})
ALL_IDS = sorted(SEND_IDS | RECV_IDS)


def apply_event(totals: Dict[int, list], event) -> bool:
    """Add one ETW event `(event_id, fields)` to `totals` {pid: [sent, received]}.

    Returns True if it was a network data event. Split out so the counting is
    tested without a trace session (which needs elevation).
    """
    try:
        event_id, fields = event
    except (TypeError, ValueError):
        return False
    if event_id not in SEND_IDS and event_id not in RECV_IDS:
        return False
    try:
        pid, size = int(fields["PID"]), int(fields["size"])
    except (KeyError, TypeError, ValueError):
        logger.debug("network event %s without a usable PID/size", event_id)
        return False
    slot = totals.setdefault(pid, [0, 0])
    slot[0 if event_id in SEND_IDS else 1] += size
    return True


class NetworkTrace:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: Dict[int, list] = {}
        self._prev: Dict[int, Tuple[int, int]] = {}
        self._prev_t: Optional[float] = None
        self._job = None
        self.rates: Dict[int, Tuple[float, float]] = {}       # pid -> (send B/s, recv B/s)
        self.events_seen = 0
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._job is not None

    # ---- lifecycle ------------------------------------------------------------------

    def start(self) -> Tuple[bool, str]:
        if self._job is not None:
            return True, "already running"
        from core.admin_utils import is_admin
        if not is_admin():
            self.last_error = "per-process network needs administrator rights (the kernel only lets an elevated process trace it)"
            return False, self.last_error
        try:
            import etw
        except ImportError as e:
            self.last_error = f"the ETW helper is not installed ({e})"
            logger.warning(self.last_error)
            return False, self.last_error
        _stop_named_session()                # a stale session from a crashed run
        try:
            provider = etw.ProviderInfo("Microsoft-Windows-Kernel-Network", etw.GUID(PROVIDER_GUID))
            job = etw.ETW(providers=[provider], event_callback=self._on_event,
                          session_name=SESSION_NAME, event_id_filters=ALL_IDS)
            job.start()
        except Exception as e:               # ETW raises a wide variety; all become a message
            self.last_error = f"could not start the network trace: {e}"
            logger.warning(self.last_error, exc_info=True)
            return False, self.last_error
        self._job = job
        self._prev_t = time.monotonic()
        atexit.register(self.stop)
        return True, "tracing"

    def stop(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            try:
                job.stop()
            except Exception as e:
                logger.warning("stopping the network trace: %s", e)
        _stop_named_session()
        self.rates = {}
        with self._lock:
            self._prev = {}

    def _on_event(self, event) -> None:
        with self._lock:
            if apply_event(self._totals, event):
                self.events_seen += 1

    # ---- reading --------------------------------------------------------------------

    def sample_rates(self) -> Dict[int, Tuple[float, float]]:
        """Bytes/second per PID since the last call. First call answers {}."""
        now = time.monotonic()
        with self._lock:
            current = {pid: (v[0], v[1]) for pid, v in self._totals.items()}
        prev, self._prev = self._prev, current
        last_t, self._prev_t = self._prev_t, now
        # "No earlier sample" is about TIME, not about the earlier sample being
        # empty: an idle first interval is a valid baseline, and testing
        # `not prev` here dropped the first burst of traffic of every session.
        if last_t is None or now <= last_t:
            self.rates = {}
            return self.rates
        dt = now - last_t
        rates = {}
        for pid, (sent, recv) in current.items():
            p_sent, p_recv = prev.get(pid, (0, 0))
            up, down = (sent - p_sent) / dt, (recv - p_recv) / dt
            if up > 0 or down > 0:
                rates[pid] = (up, down)
        self.rates = rates
        return rates

    def total_of(self, pid: int) -> Tuple[int, int]:
        with self._lock:
            v = self._totals.get(pid)
        return (v[0], v[1]) if v else (0, 0)


def _stop_named_session() -> None:
    """Stop our session by NAME. Harmless when there is none."""
    import subprocess
    try:
        subprocess.run(["logman", "stop", SESSION_NAME, "-ets"], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("logman stop %s: %s", SESSION_NAME, e)


_shared: Optional[NetworkTrace] = None


def shared() -> NetworkTrace:
    """The one trace the Processes tab and its Network column both read."""
    global _shared
    if _shared is None:
        _shared = NetworkTrace()
    return _shared


def rate_of(pid: int) -> float:
    """Total bytes/s (send + receive) for `pid` at the last sample, else 0."""
    up_down = shared().rates.get(pid)
    return (up_down[0] + up_down[1]) if up_down else 0.0
