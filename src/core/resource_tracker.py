"""ResourceTracker
===================

A lightweight utility used by the Game Mode engine to keep track of all OS resources
created during a `turn_on` cycle and guarantee deterministic cleanup on `turn_off`.

The tracker records:
- Process handles (via `win32api` or `ctypes` wrappers)
- Registry keys (opened with `winreg`)
- Timers (timer objects returned by `system.set_timer_resolution` or callables)
- Workers (`Worker` instances from `src.core.worker`)
- COM objects (`COMWorker`)
- File handles opened directly via `open`

Each resource type implements a tiny ``close`` protocol; the tracker stores a
callable that will be invoked during cleanup. All cleanup actions are wrapped in
`try/except` so a failure in one resource does not stop the others.

Usage pattern in ``session.py``::

    from core.resource_tracker import ResourceTracker
    tracker = ResourceTracker()
    # ... whenever a resource is created ...
    tracker.track_handle(handle)
    tracker.track_worker(my_worker)
    # at the end of the wave
    tracker.cleanup(wave_id=1)

The JSON snapshot written to ``<AppData>/WindowsTweaker/GameMode/state_snapshot_<wave>.json``
contains a simple list of resource identifiers for post‑mortem analysis.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

_APP_DATA_DIR = os.getenv("APPDATA") or tempfile.gettempdir()
_BASE_DIR = os.path.join(_APP_DATA_DIR, "WindowsTweaker", "GameMode")
try:
    os.makedirs(_BASE_DIR, exist_ok=True)
except Exception:
    pass


class ResourceTracker:
    """Track and clean up OS resources for a single Game Mode wave.

    The tracker is thread-safe and can be used across worker callbacks.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self._cleanup_actions: List[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._snapshot: List[dict] = []
        self._wave_id: Optional[int] = None
        self._base_dir = base_dir or _BASE_DIR
        try:
            os.makedirs(self._base_dir, exist_ok=True)
        except Exception:
            pass

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._cleanup_actions)

    def get_snapshot(self) -> List[dict]:
        with self._lock:
            return list(self._snapshot)

    # ---------------------------------------------------------------------
    # Public registration helpers – each stores a lambda that knows how to
    # release the resource.
    # ---------------------------------------------------------------------
    def track_handle(self, handle: int, description: str = "handle") -> None:
        """Register a raw Windows handle (int) for later ``CloseHandle``."""
        def _close():
            try:
                import ctypes
                close_fn = getattr(ctypes.windll.kernel32, "CloseHandle", None)
                if close_fn and handle:
                    close_fn(handle)
            except Exception as e:
                logger.warning("Failed to close handle %s: %s", handle, e)

        with self._lock:
            self._cleanup_actions.append(_close)
            self._snapshot.append({"type": "handle", "handle": handle, "desc": description})

    def track_worker(self, worker: Any) -> None:
        """Register a ``Worker`` instance. ``worker.cancel()`` is called on cleanup."""
        def _cancel():
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
            except Exception as e:
                logger.warning("Worker cancel failed: %s", e)

        with self._lock:
            self._cleanup_actions.append(_cancel)
            worker_id = getattr(worker, "id", None)
            if worker_id is not None and not isinstance(worker_id, (str, int, float, bool)):
                worker_id = str(worker_id)
            self._snapshot.append({
                "type": "worker",
                "id": worker_id or str(type(worker).__name__),
            })

    def track_timer(self, timer_obj: Any, description: str = "timer") -> None:
        """Register a timer resolution object or callable restore function."""
        def _restore():
            try:
                if hasattr(timer_obj, "restore") and callable(getattr(timer_obj, "restore")):
                    timer_obj.restore()
                elif hasattr(timer_obj, "cancel") and callable(getattr(timer_obj, "cancel")):
                    timer_obj.cancel()
                elif callable(timer_obj):
                    timer_obj()
            except Exception as e:
                logger.warning("Timer restore failed: %s", e)

        with self._lock:
            self._cleanup_actions.append(_restore)
            self._snapshot.append({"type": "timer", "desc": description})

    def track_registry_key(self, key: Any, description: str = "regkey") -> None:
        """Register an opened ``winreg`` key object."""
        def _close():
            try:
                if hasattr(key, "Close"):
                    key.Close()
                elif hasattr(key, "close"):
                    key.close()
            except Exception as e:
                logger.warning("Reg key close failed: %s", e)

        with self._lock:
            self._cleanup_actions.append(_close)
            self._snapshot.append({"type": "regkey", "desc": description})

    def track_file(self, file_obj: Any, description: str = "file") -> None:
        """Register a file object opened with ``open``."""
        def _close():
            try:
                if hasattr(file_obj, "close"):
                    file_obj.close()
            except Exception as e:
                logger.warning("File close failed: %s", e)

        with self._lock:
            self._cleanup_actions.append(_close)
            self._snapshot.append({"type": "file", "desc": description})

    def track_custom(self, callback: Callable[[], None], description: str = "custom") -> None:
        """Register a custom cleanup callable."""
        with self._lock:
            self._cleanup_actions.append(callback)
            self._snapshot.append({"type": "custom", "desc": description})

    # ---------------------------------------------------------------------
    # Cleanup & snapshot
    # ---------------------------------------------------------------------
    def cleanup(self, wave_id: Optional[int] = None) -> None:
        """Execute all registered cleanup actions in reverse registration order."""
        with self._lock:
            if wave_id is not None:
                self._wave_id = wave_id
            actions = list(self._cleanup_actions)
            snapshot_items = list(self._snapshot)
            active_wave = self._wave_id

        # Run actions in reverse order of registration – mirrors RAII.
        for action in reversed(actions):
            try:
                action()
            except Exception as e:
                logger.error("Resource cleanup error: %s", e)

        # Write snapshot JSON for diagnostic purposes.
        if active_wave is not None and snapshot_items:
            snapshot_path = os.path.join(self._base_dir, f"state_snapshot_{active_wave}.json")
            try:
                with open(snapshot_path, "w", encoding="utf-8") as f:
                    json.dump(snapshot_items, f, indent=2, default=str)
            except Exception as e:
                logger.warning("Failed to write state snapshot %s: %s", snapshot_path, e)

        # Reset internal state for reuse in next wave.
        with self._lock:
            self._cleanup_actions.clear()
            self._snapshot.clear()
            self._wave_id = None


_GLOBAL_TRACKER: Optional[ResourceTracker] = None
_GLOBAL_LOCK = threading.Lock()


def get_resource_tracker() -> ResourceTracker:
    global _GLOBAL_TRACKER
    with _GLOBAL_LOCK:
        if _GLOBAL_TRACKER is None:
            _GLOBAL_TRACKER = ResourceTracker()
        return _GLOBAL_TRACKER
