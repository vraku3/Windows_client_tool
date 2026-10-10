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
import time
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .engine import curves as cv
from .engine import gpu_kmt, lhm_bridge, view
from .engine.controller import FanController
from .engine.model import Sensor

logger = logging.getLogger(__name__)

CURVES_KEY = "modules.thermal_control.curves"
GPU_CURVES_KEY = "modules.thermal_control.gpu_curves"           # {gpu name: [[C, %], ...]}
GPU_FACTORY_KEY = "modules.thermal_control.gpu_before"          # {gpu name: {factory, curve}}
#: Written BEFORE a GPU Identify takes the fan to 100%, removed once it is put
#: back. The curve lives in GPU firmware and survives the app, so a crash in
#: those 20 s would otherwise leave the card at full speed until reboot.
GPU_IDENTIFY_KEY = "modules.thermal_control.gpu_identify_restore"   # {gpu name: {factory, curve, zero}}
GROUPS_KEY = "modules.thermal_control.fan_groups"                   # {control id: cpu|case|none}
GPU_PREFIX_ID = "gpu:"
CONTROL_MS = 2000          # curve tick: fans and temperatures do not move faster
VIEW_MS = 1000             # while someone is looking at the sensor list


class ThermalService(QObject):
    #: The running service, for readers elsewhere in the app (Dashboard
    #: Thermals, Overview, System Report) -- they never open the hardware a
    #: second time.
    instance: Optional["ThermalService"] = None

    updated = pyqtSignal(object)            # List[Sensor]
    state_changed = pyqtSignal(str)         # a human sentence about what is going on
    gpu_fans_changed = pyqtSignal(object)   # List[adlx.GpuFanStatus]
    identify_changed = pyqtSignal()         # an Identify started or ended

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
        self.gpu_fans: list = []
        self.adlx_dir = ""
        self._identify: Dict[str, Tuple[float, QTimer]] = {}     # target -> (ends at, timer)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        atexit.register(self._atexit)
        ThermalService.instance = self

    # ---- lifecycle ------------------------------------------------------------------

    @property
    def full(self) -> bool:
        return self.controller is not None

    def start(self) -> None:
        """Open the hardware off the UI thread; GPU-only if it cannot be."""
        self._check_gpu_fans()
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
        logger.info("thermal: hardware opened; %d curve(s) saved, %d active, recovered %s",
                    len(self.controller.curves.curves), len(self.controller.curves.enabled()),
                    self.recovered or "nothing")
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
        try:
            self._timer.stop()
        except RuntimeError as e:
            # From the atexit net, Qt has already destroyed the timer. That
            # must never stop the part that matters: handing the fans back.
            logger.info("thermal shutdown after Qt teardown: %s", e)
        for worker in self._workers:
            worker.cancel()
        for name in self._identify_names():
            try:
                self._restore_gpu(name)          # synchronous: the app is going away
            except Exception as e:  # AdlxError, or the library already unloaded at exit
                logger.error("could not put %s back after Identify at shutdown: %s", name, e)
        self._identify.clear()
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

    def read_now(self) -> List[Sensor]:
        """A fresh reading, synchronously, from any thread: the open hardware
        (the bridge serialises calls with its own lock) or, unelevated, the
        driverless GPU reader. Never opens the hardware a second time."""
        controller = self.controller
        if controller is not None:
            try:
                return controller.read()
            except Exception as e:  # a .NET exception from the driver path
                logger.warning("thermal read_now failed: %s", e)
        return gpu_kmt.read_gpus()

    # ---- curves -------------------------------------------------------------------

    def curves(self) -> cv.CurveSet:
        return self.controller.curves if self.controller else cv.CurveSet()

    def save_curve(self, curve: cv.FanCurve) -> List[str]:
        """Store and (if enabled) start a curve; returns why it was refused."""
        problems = cv.validate(curve)
        if curve.control_id.startswith("/gpu"):
            problems.append("GPU fans are set through the GPU's own curve (the GPU entry), not as a header")
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

    # ---- Identify: full speed for a while, then back ------------------------------------
    #
    # A target is a header id or "gpu:<name>". Clicking again restarts the
    # clock; Stop ends everything at once. Headers go back to their curve or
    # the BIOS; a GPU goes back to exactly what it had (see GPU_IDENTIFY_KEY).

    def identifying(self) -> Dict[str, int]:
        """{target: whole seconds left} for everything spinning right now."""
        now = time.monotonic()
        return {t: max(0, int(round(end - now))) for t, (end, _tm) in self._identify.items()}

    def _arm(self, target: str, seconds: int, finish: Callable[[], None]) -> None:
        old = self._identify.pop(target, None)
        if old is not None:
            old[1].stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._finish(target, finish))
        timer.start(seconds * 1000)
        self._identify[target] = (time.monotonic() + seconds, timer)
        self.identify_changed.emit()

    def _finish(self, target: str, finish: Callable[[], None]) -> None:
        entry = self._identify.pop(target, None)
        if entry is not None:
            entry[1].stop()
        finish()
        self.identify_changed.emit()

    def identify(self, control_id: str, seconds: int = view.IDENTIFY_SECONDS) -> str:
        """Spin one header to 100% for `seconds`, then put it back. "" or why not."""
        if self.controller is None:
            return self.reason or "the hardware is not open yet"
        controller = self.controller
        try:
            controller.boost(control_id)
        except Exception as e:  # KeyError for a vanished header, or a driver error
            logger.error("could not spin up %s: %s", control_id, e)
            return str(e)
        self._arm(control_id, seconds, lambda: self._end_identify(controller, control_id))
        return ""

    def _end_identify(self, controller, control_id: str) -> None:
        if controller is not self.controller or self._stopped:
            return                              # shut down meanwhile: everything was handed back
        try:
            controller.end_boost(control_id)
        except Exception as e:  # a .NET exception from the driver path
            logger.error("could not end the spin-up of %s: %s", control_id, e)
            self.errors = [f"{control_id}: {e}"]
        self.tick()

    def group_overrides(self) -> Dict[str, str]:
        return dict(self._config_get(GROUPS_KEY, {}) or {})

    def set_group(self, control_id: str, group: str) -> None:
        groups = self.group_overrides()
        groups[control_id] = group
        self._config_set(GROUPS_KEY, groups)

    def identify_group(self, group: str, seconds: int = view.IDENTIFY_SECONDS) -> Tuple[List[str], List[str]]:
        """Every fan in CPU / GPU / Case to 100%. (names started, problems)."""
        started, problems = [], []
        if group == "gpu":
            # Only GPUs with fan control: the CPU's integrated Radeon has no fan,
            # and naming it as a failure on every click would be noise.
            controllable = [st for st in self.gpu_fans if st.controllable]
            for st in controllable:
                why = self.identify_gpu(st.name, seconds)
                (problems.append(f"{st.name}: {why}") if why else started.append(st.name))
            if not controllable:
                problems.append("; ".join(st.reason for st in self.gpu_fans) or "no GPU fan control was found")
            return started, problems
        members = view.group_headers(self.sensors, self.group_overrides(), group)
        if not members:
            problems.append("no fan header is in this group" if self.full
                            else (self.reason or "the hardware is not open yet"))
        for control in members:
            why = self.identify(control.id, seconds)
            (problems.append(f"{control.name}: {why}") if why else started.append(control.name))
        return started, problems

    def stop_identify(self) -> None:
        for target in list(self._identify):
            entry = self._identify.get(target)
            if entry is not None:
                entry[1].timeout.emit()

    def identify_gpu(self, name: str, seconds: int = view.IDENTIFY_SECONDS) -> str:
        """The GPU fan to 100% (Zero RPM off, a flat curve at the top of its range)."""
        from .engine import adlx
        st = self.gpu_status(name)
        if st is None or not st.controllable or st.gpu is None:
            return st.reason if st else "this GPU is not available"
        target = GPU_PREFIX_ID + name
        fresh = target not in self._identify
        # Armed FIRST: the write job's own read-back must already see this
        # Identify, or it would take the marker for a crashed one and undo it.
        self._arm(target, seconds, lambda: self._end_gpu_identify(name))
        if fresh:
            restore = dict(self._config_get(GPU_IDENTIFY_KEY, {}) or {})
            restore[name] = {"factory": bool(st.gpu.at_factory), "curve": [list(p) for p in st.gpu.curve],
                             "zero": st.gpu.zero_rpm}
            self._config_set(GPU_IDENTIFY_KEY, restore)        # before the write, never after
            top = (st.gpu.speed_range or (0, 100))[1]
            flat = [(t, top) for t, _s in st.gpu.curve]

            def work(_w):
                if st.gpu.zero_rpm:
                    adlx.set_zero_rpm(name, False, self.adlx_dir)
                adlx.set_curve(name, flat, self.adlx_dir)
                return self._read_gpu_fans()
            self._gpu_job(work, self._gpu_fans_read)
        return ""

    def _end_gpu_identify(self, name: str) -> None:
        self._gpu_job(lambda _w: self._restore_gpu(name) or self._read_gpu_fans(), self._gpu_fans_read)

    def _restore_gpu(self, name: str) -> None:
        """Put the GPU back to what it had before Identify; clears its marker."""
        from .engine import adlx
        restore = dict(self._config_get(GPU_IDENTIFY_KEY, {}) or {})
        before = restore.get(name)
        if before is None:
            return
        if before.get("factory"):
            adlx.reset_to_factory(name, self.adlx_dir)       # it was at factory: nothing else to lose
        else:
            adlx.set_curve(name, [tuple(p) for p in before["curve"]], self.adlx_dir)
            if before.get("zero") is not None:
                adlx.set_zero_rpm(name, bool(before["zero"]), self.adlx_dir)
        restore.pop(name, None)
        self._config_set(GPU_IDENTIFY_KEY, restore)
        logger.info("thermal: %s put back after Identify (%s)", name,
                    "factory" if before.get("factory") else "its own curve")

    # ---- GPU fans (AMD ADLX; needs no elevation) ---------------------------------------
    #
    # The curve lives in the GPU's firmware, so there is no tick: a write
    # is one call, verified by reading it back. What this app remembers is
    # the curve the user saved (re-applied at start if the driver dropped
    # it) and whether the GPU was at factory before the first write -- that
    # decides whether "back to factory" may use AMD's full reset.

    def _check_gpu_fans(self) -> None:
        self._gpu_job(self._read_gpu_fans, self._gpu_fans_read)

    def _read_gpu_fans(self, _w=None):
        from .engine import adlx
        win = adlx.windows_gpus()
        self.adlx_dir = adlx.choose_copy(win, adlx.adlx_copies())
        gpus, why = adlx.read_gpus(self.adlx_dir)
        for name in list((self._config_get(GPU_IDENTIFY_KEY, {}) or {})):
            if name not in self._identify_names():
                logger.warning("thermal: %s was left at 100%% by an Identify that never ended; "
                               "putting it back", name)
                try:
                    self._restore_gpu(name)
                except adlx.AdlxError as e:
                    logger.error("could not put %s back after an interrupted Identify: %s", name, e)
                gpus, why = adlx.read_gpus(self.adlx_dir)
        statuses = adlx.diagnose(gpus, why, win)
        self._reapply_saved(statuses)
        return statuses

    def _reapply_saved(self, statuses) -> None:
        from .engine import adlx
        saved = self._config_get(GPU_CURVES_KEY, {}) or {}
        busy = self._identify_names()
        for st in statuses:
            if st.name in busy:
                continue                    # an Identify holds it at 100% on purpose
            points = saved.get(st.name)
            if not (st.controllable and points and st.gpu):
                continue
            wanted = [tuple(int(v) for v in p) for p in points]
            if wanted != [tuple(p) for p in st.gpu.curve]:
                try:
                    st.gpu.curve = adlx.set_curve(st.name, wanted, self.adlx_dir)
                    st.reason = "fan curve available (your saved curve, re-applied at start)"
                except adlx.AdlxError as e:
                    logger.warning("could not re-apply the saved curve to %s: %s", st.name, e)
                    st.reason = f"your saved curve could not be re-applied: {e}"

    def _identify_names(self) -> List[str]:
        return [t[len(GPU_PREFIX_ID):] for t in list(self._identify) if t.startswith(GPU_PREFIX_ID)]

    def _gpu_fans_read(self, statuses) -> None:
        self.gpu_fans = list(statuses)
        self.gpu_fans_changed.emit(self.gpu_fans)

    def gpu_status(self, name: str):
        return next((s for s in self.gpu_fans if s.name == name), None)

    def apply_gpu_curve(self, name: str, points) -> List[str]:
        from .engine import adlx
        st = self.gpu_status(name)
        if st is None or not st.controllable or st.gpu is None:
            return [st.reason if st else "this GPU is not available"]
        problems = adlx.validate_gpu_curve(points, st.gpu)
        if problems:
            return problems
        before = self._config_get(GPU_FACTORY_KEY, {}) or {}
        if name not in before:
            self._config_set(GPU_FACTORY_KEY, {**before, name: {"factory": bool(st.gpu.at_factory),
                                                               "curve": [list(p) for p in st.gpu.curve]}})
        self._config_set(GPU_CURVES_KEY, {**(self._config_get(GPU_CURVES_KEY, {}) or {}),
                                          name: [[int(round(t)), int(round(d))] for t, d in points]})

        def work(_w):
            adlx.set_curve(name, points, self.adlx_dir)
            return self._read_gpu_fans()
        self._gpu_job(work, self._gpu_written)
        return []

    def gpu_back_to_factory(self, name: str) -> None:
        from .engine import adlx
        before = (self._config_get(GPU_FACTORY_KEY, {}) or {}).get(name)
        saved = dict(self._config_get(GPU_CURVES_KEY, {}) or {})
        saved.pop(name, None)
        self._config_set(GPU_CURVES_KEY, saved)

        def work(_w):
            if before is None or before.get("factory", True):
                adlx.reset_to_factory(name, self.adlx_dir)
            else:                       # it had other tuning: put back only the curve it had
                adlx.set_curve(name, [tuple(p) for p in before["curve"]], self.adlx_dir)
            return self._read_gpu_fans()
        self._gpu_job(work, self._gpu_written)

    def set_gpu_zero_rpm(self, name: str, on: bool) -> None:
        from .engine import adlx

        def work(_w):
            adlx.set_zero_rpm(name, on, self.adlx_dir)
            return self._read_gpu_fans()
        self._gpu_job(work, self._gpu_written)

    def _gpu_written(self, statuses) -> None:
        self._gpu_fans_read(statuses)
        self.state_changed.emit("GPU fan settings written and read back.")

    def _gpu_job(self, fn, on_result) -> None:
        from core.worker import Worker
        pool = getattr(self._app, "thread_pool", None)
        if pool is None:
            try:
                on_result(fn(None))
            except Exception as e:  # AdlxError, or the library failing outright
                self._gpu_failed(str(e))
            return
        worker = Worker(fn)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(self._gpu_failed)
        self._workers = self._workers[-3:] + [worker]
        pool.start(worker)

    def _gpu_failed(self, message) -> None:
        logger.warning("GPU fan operation failed: %s", message)
        self.errors = [f"GPU fan: {message}"]
        self.state_changed.emit(f"GPU fan: {message}")

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
