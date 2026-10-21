"""Worker thread pool utilities for background tasks.

This module provides safe worker implementation with signal emission
for progress updates, error handling, and cancellation support.
"""

import logging
import weakref
from threading import Lock
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

logger = logging.getLogger(__name__)


def _emit(signals: Any, name: str, *args: Any) -> None:
    """Emit `name` on `signals`, unless Qt has already destroyed it.

    QApplication teardown destroys every QObject, including a running
    worker's `WorkerSignals`. PyQt then answers any access to it with
    `RuntimeError: wrapped C/C++ object ... has been deleted`. Nobody is
    listening at that point -- the receivers went down with it -- so there is
    nothing to deliver and nothing to report; the only wrong move is to let
    the exception out of `run()`, which is a QRunnable on a Qt thread.

    Deliberately narrow: only the "has been deleted" RuntimeError is
    swallowed. A slot that raises must still surface.
    """
    try:
        getattr(signals, name).emit(*args)
    except RuntimeError as exc:
        if "has been deleted" not in str(exc):
            raise
        logger.debug("signals gone before %s could be emitted", name)


#: Cancelled workers whose `finished` has not been delivered yet (see `retire`).
_RETIRING: set = set()


#: How long a retired worker is still held once its `finished` was emitted.
RELEASE_AFTER_MS = 2000


def _release_later(worker) -> None:
    if worker is None:
        return
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(RELEASE_AFTER_MS, lambda: _RETIRING.discard(worker))


def retire(workers: list) -> None:
    """Cancel every worker in `workers` and empty the list -- SAFELY.

    Clearing the list right after `cancel()` dropped the last Python reference
    to a worker that was still running. When it ended, its `WorkerSignals` was
    freed while its queued `cancelled`/`finished` were still waiting in the
    event loop, and delivering them ran a freed closure: an access violation
    (category_group, every run of test_cleanup_final_review_ui, 2026-10-21).
    Here each worker is kept alive until its `finished` -- the last signal it
    ever sends -- has been delivered, then released.
    """
    for worker in list(workers):
        worker.cancel()
        if not isinstance(worker, Worker):
            continue
        _RETIRING.add(worker)
        if worker.finished_emitted:
            # Already sent: any queued delivery is ahead of this timer.
            _release_later(worker)
        else:
            # Only RETIRED workers get this connection. Connecting it in every
            # Worker's __init__ gave every worker an extra queued slot, and each
            # one an owner dropped early ran freed memory -- access violations
            # across the whole suite (2026-10-21).
            ref = weakref.ref(worker)
            worker.signals.finished.connect(lambda: _release_later(ref()))
            if worker.finished_emitted:      # it finished while we connected
                _release_later(worker)
    workers.clear()


class WorkerSignals(QObject):
    """Signal definitions for Worker class."""

    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)  # thread-safe log output
    cancelled = pyqtSignal()
    finished = pyqtSignal()  # always emitted after result or error


class Worker(QRunnable):
    """Thread worker with signal emission for safe async operations.

    Usage pattern:
        def _process(worker):
            def do_work():
                result = do_implementation(worker)
                return result

            w = Worker(_process)
            w.signals.result.connect(handler)
            app.thread_pool.start(w)

        Args:
            fn: Callable that accepts (worker: Worker) as first argument
            *args: Positional args to pass to fn
            **kwargs: Keyword args to pass to fn

        Signals:
            result: Emitted with computed result value
            error: Emitted with exception message string
            progress: Emitted with progress percentage (0-100)
            log_line: Emitted with log message string
            cancelled: Emitted if worker cancelled
            finished: Emitted when worker completes
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Create worker instance.

        Args:
            fn: Worker function accepting (worker, *args, **kwargs)
            *args: Args to pass to worker function
            **kwargs: Keyword args to pass to worker function
        """
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancelled = False
        self._cancel_lock = Lock()
        # Set (on the worker thread) just before `finished` -- always the LAST
        # signal -- is emitted; `retire()` holds a worker until it is delivered.
        self.finished_emitted = False

    def run(self) -> Any:
        """Execute worker function.

        Emits progress updates, result, errors via signals.
        Always emits finished, even when cancelled before execution starts.
        """
        with self._cancel_lock:
            if self._cancelled:
                _emit(self.signals, "cancelled")
                self.finished_emitted = True
                _emit(self.signals, "finished")
                return

        try:
            result = self.fn(self, *self.args, **self.kwargs)
            if not self._cancelled:
                _emit(self.signals, "result", result)
            else:
                _emit(self.signals, "cancelled")
        except PermissionError as e:
            # The OS said no. The caller handles it and shows it; a full
            # ERROR traceback in the session log makes a routine refusal
            # read as a crash, and that log is the first thing read when
            # picking this project back up.
            logger.warning("Worker refused: %s", e)
            _emit(self.signals, "error", str(e))
        except Exception as e:
            logger.exception("Worker error: %s", e)
            _emit(self.signals, "error", str(e))
        finally:
            self.finished_emitted = True
            _emit(self.signals, "finished")

    def cancel(self) -> bool:
        """Cancel worker and prevent future result emission.

        Returns:
            True if worker was cancelled or already cancelled
        """
        with self._cancel_lock:
            self._cancelled = True
            return self._cancelled

    @property
    def is_cancelled(self) -> bool:
        """Check if worker has been cancelled (thread-safe)."""
        with self._cancel_lock:
            return self._cancelled


class COMWorker(Worker):
    """Worker subclass for COM/COM-related operations.

    This worker initializes COM on the thread before running and
    cleans up. Use this for any worker calling win32com.client or
    COM-related pythoncom modules.
    """

    def run(self) -> Any:
        """Run COM-backed worker function.

        Initializes COM with CoInitialize before calling super().run()
        and uninitializes with CoUninitialize in finally block.

        Returns:
            The result from the worker function (preserved from super().run()).
        """
        import pythoncom

        pythoncom.CoInitialize()
        try:
            return super().run()
        finally:
            pythoncom.CoUninitialize()

