"""QRunnable for MTP device scanning.

Like ScanWorker but uses Shell COM instead of the filesystem scanner.
Handles COM initialization/teardown on the worker thread.
"""
import time

import pythoncom
from PyQt6.QtCore import QRunnable

from ..scan.mtp_scanner import mtp_enumerate
from ..store.node_store import DIR, NodeStore
from ..store.rollup import rollup
from .scan_worker import ScanSignals


class MtpScanWorker(QRunnable):

    def __init__(self, device_name: str, shell_path: str,
                 capacity: int = 0, free_space: int = 0) -> None:
        super().__init__()
        self.signals = ScanSignals()
        self.device_name = device_name
        self.shell_path = shell_path
        self.capacity = capacity
        self.free_space = free_space
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def run(self) -> None:
        try:
            pythoncom.CoInitialize()
        except Exception as exc:                         # noqa: BLE001
            self.signals.failed.emit(f"MTP: {type(exc).__name__}: {exc}")
            return
        try:
            if not self.capacity:
                from ..scan.mtp_scanner import mtp_device_storage
                self.capacity, self.free_space = mtp_device_storage(self.shell_path)
            t0 = time.perf_counter()
            store = NodeStore()
            root = store.add(-1, self.device_name, attrs=DIR)
            node_count, error_count, errors = mtp_enumerate(
                self.shell_path, store, root,
                on_batch=self.signals.batch_ready.emit,
                should_cancel=self.is_cancelled)
            if self._cancelled:
                self.signals.cancelled.emit()
                return
            rollup(store)

            class _Result:
                pass

            result = _Result()
            result.store = store
            result.root = root
            result.node_count = node_count
            result.excluded = 0
            result.engine = "mtp"
            result.volume_info = None
            result.complete = error_count == 0
            result.errors = errors
            result.error_count = error_count
            result.elapsed = time.perf_counter() - t0
            result.mtp_capacity = self.capacity
            result.mtp_free = self.free_space
            self.signals.finished.emit(result)
        except Exception as exc:                         # noqa: BLE001
            self.signals.failed.emit(f"MTP: {type(exc).__name__}: {exc}")
        finally:
            pythoncom.CoUninitialize()
