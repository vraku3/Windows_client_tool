"""Runs the curves against the real headers, and cleans up after a crash. No Qt.

A SuperIO chip keeps the last duty software gave it. If the app dies while
a header is under a curve, that fan is stuck at that speed until something
hands it back to the BIOS. So, while any header is under software control,
`marker_path` lists them; a clean stop removes it. On the next start,
`recover()` finds a marker that is still there -- the app did not stop
cleanly -- and hands those headers back BEFORE anything else happens.
"""
import json
import logging
import os
import threading
from typing import Callable, Dict, List, Optional, Tuple

from . import curves as cv
from .model import TEMPERATURE, Sensor

logger = logging.getLogger(__name__)


class FanController:
    def __init__(self, bridge, marker_path: str,
                 extra_sensors: Optional[Callable[[], List[Sensor]]] = None) -> None:
        self.bridge = bridge
        self.marker_path = marker_path
        self._extra = extra_sensors
        self.curves = cv.CurveSet()
        self._states: Dict[str, cv.CurveState] = {}
        self._defaults: Dict[str, Optional[dict]] = {}     # control id -> BIOS mode/PWM
        self.last_errors: List[str] = []
        # tick() runs on a worker; shutdown() on the UI thread at exit. Without
        # this, a tick in flight could set a duty AFTER shutdown handed every
        # header back, leaving a fan under software control with no app.
        self._apply_lock = threading.Lock()
        self.closed = False

    # ---- crash recovery -----------------------------------------------------------------

    def recover(self) -> List[str]:
        """Hand back any header a previous run left under software control.

        A plain release in THIS process would restore nothing: the chip's BIOS
        mode was captured in the dead process's memory (measured -- see
        `LhmBridge.restore_saved_default`). So the marker carries those values
        and they are put back first; a header with none saved falls back to a
        plain release and is reported, because it may still be stuck."""
        saved = self._read_marker()
        if not saved:
            return []
        self.bridge.read()                       # learn the controls before releasing them
        released = []
        for control_id, values in saved.items():
            try:
                if values:
                    self.bridge.restore_saved_default(control_id, values["mode"], values["pwm"])
                else:
                    self.bridge.release(control_id)
                    self.last_errors.append(f"{control_id}: no saved BIOS mode; it may still be fixed "
                                            "at its last speed until the PC restarts")
                released.append(control_id)
            except Exception as e:  # a .NET exception from the driver path
                logger.error("could not hand %s back to the BIOS after a crash: %s", control_id, e)
                self.last_errors.append(f"{control_id}: {e}")
        self._defaults.clear()
        self._write_marker([])
        if released:
            logger.warning("a previous run did not release %s; handed back to the BIOS", released)
        return released

    def _read_marker(self) -> Dict[str, Optional[dict]]:
        try:
            with open(self.marker_path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning("fan-control marker unreadable (%s): %s", self.marker_path, e)
            return {}
        controls = data.get("controls", [])
        if isinstance(controls, list):          # an id list carries no BIOS values
            return {cid: None for cid in controls}
        return {cid: (v or None) for cid, v in controls.items()}

    def _write_marker(self, ids: List[str]) -> None:
        try:
            if ids:
                for cid in ids:
                    if cid not in self._defaults:
                        self._defaults[cid] = self._capture_default(cid)
                os.makedirs(os.path.dirname(self.marker_path) or ".", exist_ok=True)
                with open(self.marker_path, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(),
                               "controls": {cid: self._defaults.get(cid) for cid in sorted(ids)}}, f)
            elif os.path.exists(self.marker_path):
                os.remove(self.marker_path)
        except OSError as e:
            logger.error("could not update the fan-control marker %s: %s", self.marker_path, e)

    def _capture_default(self, control_id: str) -> Optional[dict]:
        capture = getattr(self.bridge, "saved_default", None)
        if capture is None:
            return None
        try:
            return capture(control_id)
        except Exception as e:  # reflection into the library can fail on another chip family
            logger.warning("could not capture the BIOS fan mode of %s: %s", control_id, e)
            return None

    # ---- the loop -----------------------------------------------------------------------

    def read(self) -> List[Sensor]:
        sensors = list(self.bridge.read())
        if self._extra is not None:
            sensors += self._extra()
        return sensors

    def tick(self) -> Tuple[List[Sensor], List[cv.Decision]]:
        """Read every sensor, apply every enabled curve. Never raises."""
        self.last_errors = []
        try:
            sensors = self.read()
        except Exception as e:  # the whole read failing must still drive fans to safety
            logger.error("sensor read failed: %s", e)
            self.last_errors.append(f"sensor read failed: {e}")
            sensors = []
        temps = {s.id: s.value for s in sensors if s.kind == TEMPERATURE}
        with self._apply_lock:
            if self.closed:
                return sensors, []
            decisions = cv.step(self.curves, temps, self._states)
            for d in decisions:
                try:
                    self.bridge.set_percent(d.control_id, d.percent)
                except Exception as e:  # KeyError for a header that vanished, or a driver error
                    logger.error("could not set %s to %.0f%%: %s", d.control_id, d.percent, e)
                    self.last_errors.append(f"{d.control_id}: {e}")
            self._write_marker(self.bridge.touched)
        return sensors, decisions

    def release(self, control_id: str) -> None:
        """Hand one header back to the BIOS (a curve was disabled or removed)."""
        with self._apply_lock:
            self._states.pop(control_id, None)
            self.bridge.release(control_id)
            self._write_marker(self.bridge.touched)

    def shutdown(self) -> List[Tuple[str, str]]:
        """Hand every header back. The marker is removed only if all of them went."""
        with self._apply_lock:
            self.closed = True
            failed = self.bridge.release_all()
            self._write_marker([cid for cid, _err in failed])
        return failed
