"""One restore point per Debloat "session," reused across Apps / Privacy /
AI applies rather than one per click. Windows' own restore-point frequency
floor makes the difference between "3 checkpoints" and "1 checkpoint, 3
apply operations recorded against it" the difference between silently
losing the 2nd/3rd protection and genuinely having it."""
import threading
import time as _time
from typing import Callable, Optional

SESSION_WINDOW_MINUTES = 15


class DebloatSession:
    def __init__(self, create_rp: Callable[[str], str],
                now: Optional[Callable[[], float]] = None):
        self._create_rp = create_rp
        self._now = now or _time.time
        self._rp_id: Optional[str] = None
        self._last_at: Optional[float] = None
        self._lock = threading.Lock()

    def restore_point_id(self, label: str) -> str:
        with self._lock:
            now = self._now()
            if self._rp_id is not None and self._last_at is not None and \
                    now - self._last_at <= SESSION_WINDOW_MINUTES * 60:
                self._last_at = now
                return self._rp_id
            self._rp_id = self._create_rp(label)
            self._last_at = now
            return self._rp_id
