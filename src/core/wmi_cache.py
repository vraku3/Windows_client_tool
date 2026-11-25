"""Thread-safe short-TTL cache for expensive WMI / CIM / system queries (Qt-free).

Multiple modules (Dashboard, Driver Manager, System Health, Hardware Info)
query overlapping WMI/CIM classes (Win32_PnPEntity, Win32_VideoController,
Win32_DiskDrive, etc.). Caching identical reads for a short TTL avoids
redundant WMI round-trips and CPU spikes when switching tabs.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class WmiCache:
    """Thread-safe TTL cache keyed by query identifier."""

    def __init__(self, default_ttl: float = 15.0) -> None:
        self._default_ttl = float(default_ttl)
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[float, Any]] = {}

    def get_or_query(
        self,
        key: str,
        fn: Callable[[], Any],
        ttl: Optional[float] = None,
    ) -> Any:
        """Return cached value if fresh, otherwise run `fn()` and store it."""
        effective_ttl = self._default_ttl if ttl is None else float(ttl)
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                expires_at, value = hit
                if now < expires_at:
                    return value

        value = fn()
        with self._lock:
            self._entries[key] = (time.monotonic() + effective_ttl, value)
        return value

    def invalidate(self, prefix: Optional[str] = None) -> int:
        """Drop all cached entries (or only keys starting with `prefix`)."""
        with self._lock:
            if prefix is None:
                count = len(self._entries)
                self._entries.clear()
                return count
            keys = [k for k in self._entries if k.startswith(prefix)]
            for k in keys:
                del self._entries[k]
            return len(keys)


_GLOBAL_CACHE = WmiCache(default_ttl=15.0)


def shared_wmi_cache() -> WmiCache:
    """Return the process-wide shared WMI/CIM query cache."""
    return _GLOBAL_CACHE
