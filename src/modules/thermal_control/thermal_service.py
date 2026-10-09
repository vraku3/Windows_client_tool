"""The thermal service: owns the hardware, runs the curves, always cleans up.

Owned by ThermalControlModule, not by its widget, so enabled curves keep
running whether or not the tab was ever opened -- a fan curve that only
works while you look at it is not a fan curve. Every hardware call runs on
a worker; the bridge serialises them with its own lock.

Order at start (elevated, PawnIO present): open the library, run crash
recovery (hand back anything a previous run left), THEN load and apply the
saved curves. At app exit `shutdown()` hands every header back; an atexit
hook repeats it in case the normal path never runs.
"""
import atexit
import logging
import os
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .engine import curves as cv
from .engine import gpu_kmt, lhm_bridge
from .engine.controller import FanController
from .engine.model import Sensor

logger = logging.getLogger(__name__)

CURVES_KEY = "modules.thermal_control.curves"
CONTROL_MS = 2000          # curve tick: fans and temperatures do not move faster
VIEW_MS = 1000             # while someone is looking at the sensor list


class ThermalService(QObject):
    updated = pyqtSignal(object)            # List[Sensor]
    state_changed = pyqtSignal(str)         # a human sentence about what is going on

    def __init__(self, app, marker_path: str) -> None:
        super().__init__()
        self._app = app
        self.marker_path = marker_path
        self.reason = lhm_bridge.unavailable_reason()
        self.controller: Optional[FanController] = None
        self.sensors: List[Sensor] = []
        self.recovered: List[str] = []
        self.errors: List[str] = []
        self._viewers = 0
        self._busy = False
        self._workers: list = []
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        atexit.register(self._atexit)

    # ---- lifecycle ------------------------------------------------------------------

    @property
    def full(self) -> bool:
        return self.controller is not None

    def start(self) -> None:
        """Open the hardware off the UI thread; GPU-only if it cannot be."""
        if self.reason:
            self.state_changed.emit(f"GPU only: {self.reason}.")
            self._reschedule()
            return
        self._run(self._open, self._opened)

    def _open(self, _worker):
        bridge = lhm_bridge.LhmBridge()
        bridge.open()
        controller = FanController(bridge, self.marker_path, extra_sensors=gpu_kmt.read_gpus)
        recovered = controller.recover()
        controller.curves = cv.CurveSet.from_list(self._config_get(CURVES_KEY, []))
        return controller, recovered

    def _opened(self, result) -> None:
        self._busy = False
        if self._stopped:
            result[0].shutdown()
            return
        self.controller, self.recovered = result
        self.errors = list(self.controller.last_errors)
        message = f"{len(self.controller.curves.enabled())} fan curve(s) active."
        if self.recovered:
            message += (f" The last run did not stop cleanly; {len(self.recovered)} fan(s) "
                        "were handed back to the BIOS first.")
        self.state_changed.emit(message)
        self._reschedule()
        self.tick()

    def shutdown(self) -> None:
        """Hand every fan back to the BIOS. Safe to call more than once."""
        self._stopped = True
        self._timer.stop()
        for worker in self._workers:
            worker.cancel()
        if self.controller is not None:
            failed = self.controller.shutdown()
            if failed:
                logger.error("fans NOT handed back to the BIOS: %s", failed)
            try:
                self.controller.bridge.close()
            except Exception as e:  # the library's own Close() reaching the driver
                logger.error("closing the hardware library failed: %s", e)
            self.controller = None

    def _atexit(self) -> None:
        if not self._stopped:
            self.shutdown()

    # ---- polling ------------------------------------------------------------------

    def add_viewer(self) -> None:
        self._viewers += 1
        self._reschedule()
        self.tick()

    def remove_viewer(self) -> None:
        self._viewers = max(0, self._viewers - 1)
        self._reschedule()

    def _reschedule(self) -> None:
        if self._stopped:
            return
        active = self.full and bool(self.controller.curves.enabled())
        interval = VIEW_MS if self._viewers else (CONTROL_MS if active else 0)
        if interval:
            self._timer.start(interval)
        else:
            self._timer.stop()

    def tick(self) -> None:
        if self._busy or self._stopped:
            return
        controller = self.controller
        self._run(lambda _w: controller.tick()[0] if controller else gpu_kmt.read_gpus(), self._ticked)

    def _ticked(self, sensors) -> None:
        self._busy = False
        self.sensors = list(sensors)
        if self.controller is not None:
            self.errors = list(self.controller.last_errors)
        self.updated.emit(self.sensors)

    # ---- curves -------------------------------------------------------------------

    def curves(self) -> cv.CurveSet:
        return self.controller.curves if self.controller else cv.CurveSet()

    def save_curve(self, curve: cv.FanCurve) -> List[str]:
        """Store and (if enabled) start a curve; returns why it was refused."""
        problems = cv.validate(curve)
        if problems and curve.enabled:
            return problems
        if self.controller is None:
            return [self.reason or "the hardware is not open yet"]
        self.controller.curves.upsert(curve)
        if not curve.enabled:
            self.release(curve.control_id)
        self._config_set(CURVES_KEY, self.controller.curves.to_list())
        self._reschedule()
        self.tick()
        return []

    def release(self, control_id: str) -> None:
        if self.controller is None:
            return
        curve = self.controller.curves.for_control(control_id)
        if curve is not None and curve.enabled:
            curve.enabled = False
            self._config_set(CURVES_KEY, self.controller.curves.to_list())
        try:
            self.controller.release(control_id)
        except Exception as e:  # a .NET exception from the driver path
            logger.error("could not hand %s back to the BIOS: %s", control_id, e)
            self.errors.append(f"{control_id}: {e}")
        self._reschedule()

    # ---- plumbing -------------------------------------------------------------------

    def _run(self, fn: Callable, on_result: Callable) -> None:
        from core.worker import Worker
        pool = getattr(self._app, "thread_pool", None)
        if pool is None:
            on_result(fn(None))
            return
        self._busy = True
        worker = Worker(fn)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(self._failed)
        worker.signals.cancelled.connect(self._released)
        self._workers = self._workers[-3:] + [worker]
        pool.start(worker)

    def _released(self) -> None:
        self._busy = False

    def _failed(self, message) -> None:
        self._busy = False
        logger.error("thermal service: %s", message)
        self.errors = [str(message)]
        self.state_changed.emit(f"Hardware access failed: {message}")
        self._reschedule()                      # carry on GPU-only

    def _config_get(self, key: str, default):
        config = getattr(self._app, "config", None)
        return config.get(key, default) if config is not None else default

    def _config_set(self, key: str, value) -> None:
        config = getattr(self._app, "config", None)
        if config is not None:
            config.set(key, value)


def marker_path_for(app) -> str:
    base = getattr(app, "app_data_dir", None) or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "WindowsTweaker")
    return os.path.join(base, "fan_control_active.json")
