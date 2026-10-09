"""LibreHardwareMonitor through pythonnet: CPU, motherboard and fan headers. No Qt.

The bundled `vendor/lhm` is LibreHardwareMonitor 0.9.6 (MPL-2.0), the
library half only, running on the .NET Framework 4.8 that ships with
Windows. It reaches the hardware through the PawnIO kernel driver, which
needs the process to be ELEVATED. Measured on the X870E Taichi, 2026-10-09:

- unelevated: the CPU and both GPUs are listed, but `Core (Tctl/Tdie)`
  reads 0.0 -- LibreHardwareMonitor reports the refusal as zero -- and the
  motherboard has no SuperIO chips at all;
- elevated: two SuperIO chips (Nuvoton NCT6799D and NCT6686D) with eight
  controllable headers (CPU Fan #1/#2, AIO Pump, Chassis #1/#2, Water Pump,
  Chassis #3/#4), Tctl 70.6 °C, CCD1 51.6, CCD2 69.0, VRM 52.5, and every
  NVMe drive with its warning/critical limits;
- the RX 7900 XTX has NO sensors here at all; `gpu_kmt` covers it.

A temperature of exactly 0.0 is a refused read, not a reading: nothing in a
running PC is at the freezing point of water.

Fan control: `Control.SetSoftware(percent)` puts a header under manual
duty; `Control.SetDefault()` hands it back to the board's own (BIOS) curve.
Every header this bridge has touched is remembered, so `release_all()` can
hand all of them back. The chip keeps the last duty it was given if the
process dies without doing that -- see `controller.py` for how that is
contained.
"""
import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

from .model import CONTROL, FAN, TEMPERATURE, Sensor

logger = logging.getLogger(__name__)

_KINDS = {"Temperature": TEMPERATURE, "Fan": FAN, "Control": CONTROL}
_RUNTIME_LOADED = False


def vendor_dir() -> str:
    from app import _get_resource_dir
    return os.path.join(_get_resource_dir(), "vendor", "lhm")


def pawnio_installed() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\PawnIO"):
            return True
    except OSError:
        return False


def unavailable_reason() -> str:
    """'' when the full sensor set can be read; otherwise why not."""
    from core.admin_utils import is_admin
    if not os.path.exists(os.path.join(vendor_dir(), "LibreHardwareMonitorLib.dll")):
        return "the hardware-monitoring library is missing from this build"
    if not pawnio_installed():
        return "the PawnIO driver is not installed (winget install namazso.PawnIO)"
    if not is_admin():
        return "CPU, motherboard and fan readings need the app to run as administrator"
    return ""


def _load_runtime():
    global _RUNTIME_LOADED
    if not _RUNTIME_LOADED:
        from pythonnet import load
        load("netfx")
        _RUNTIME_LOADED = True
    import clr
    clr.AddReference(os.path.join(vendor_dir(), "LibreHardwareMonitorLib.dll"))
    from LibreHardwareMonitor.Hardware import Computer
    return Computer


def clean_value(kind: str, raw) -> Optional[float]:
    """None for a read that did not happen: null, NaN, or a 0.0 temperature."""
    if raw is None:
        return None
    value = float(raw)
    if value != value:                      # NaN
        return None
    if kind == TEMPERATURE and value == 0.0:
        return None
    return value


class LhmBridge:
    def __init__(self) -> None:
        self._computer = None
        self._lock = threading.Lock()
        self._controls: Dict[str, object] = {}
        self._touched: set = set()

    def open(self, cpu_only: bool = False) -> None:
        """`cpu_only` opens just the CPU: what the Flight Recorder needs, and
        it never touches the SuperIO chips the fan curves are driving."""
        Computer = _load_runtime()
        computer = Computer()
        computer.IsCpuEnabled = True
        computer.IsGpuEnabled = not cpu_only
        computer.IsMotherboardEnabled = not cpu_only
        computer.IsControllerEnabled = not cpu_only
        computer.IsStorageEnabled = not cpu_only
        computer.Open()
        self._computer = computer

    def read(self) -> List[Sensor]:
        if self._computer is None:
            return []
        out: List[Sensor] = []
        with self._lock:
            for hw in self._computer.Hardware:
                self._walk(hw, out)
        return out

    def _walk(self, hw, out: List[Sensor]) -> None:
        hw.Update()
        for s in hw.Sensors:
            kind = _KINDS.get(str(s.SensorType))
            if kind is None:
                continue
            sid = str(s.Identifier)
            control = s.Control if kind == CONTROL else None
            if control is not None:
                self._controls[sid] = control
            out.append(Sensor(sid, str(hw.Name), str(s.Name), kind,
                              clean_value(kind, s.Value), "lhm", controllable=control is not None))
        for sub in hw.SubHardware:
            self._walk(sub, out)

    def set_percent(self, control_id: str, percent: float) -> None:
        control = self._controls.get(control_id)
        if control is None:
            raise KeyError(f"no controllable header {control_id}")
        percent = max(float(control.MinSoftwareValue), min(float(control.MaxSoftwareValue), percent))
        with self._lock:
            control.SetSoftware(percent)
        self._touched.add(control_id)

    def release(self, control_id: str) -> None:
        """Hand one header back to the board's own fan curve."""
        control = self._controls.get(control_id)
        if control is None:
            return
        with self._lock:
            control.SetDefault()
        self._touched.discard(control_id)

    def release_all(self) -> List[Tuple[str, str]]:
        """Hand back every header this bridge touched; (id, error) for failures."""
        failed = []
        for control_id in list(self._touched):
            try:
                self.release(control_id)
            except Exception as e:  # a .NET exception from the driver path
                logger.error("could not hand %s back to the BIOS: %s", control_id, e)
                failed.append((control_id, str(e)))
        return failed

    @property
    def touched(self) -> List[str]:
        return sorted(self._touched)

    # ---- surviving a crash -----------------------------------------------------------
    #
    # Measured 2026-10-09: a process that took a header and died left it in
    # manual mode (NCT6799D channel 0: mode 0x00, PWM 249 = 97.6%). A NEW
    # process's SetDefault() then "restored" that same stuck state, because
    # LibreHardwareMonitor captures a header's BIOS mode the first time it
    # is written (Nct677X.SaveDefaultFanControl -> _initialFanControlMode /
    # _initialFanPwmCommand) and keeps it only in memory. BIOS automatic
    # mode on this board reads 0x40 (SmartFan IV). So the captured values
    # are read out here and persisted by the controller, and put back
    # before RestoreDefaultFanControl runs in the recovering process.

    def _chip(self, control_id: str):
        """(chip object, channel index) behind a control id like
        '/lpc/nct6799d/0/control/1'."""
        from System.Reflection import BindingFlags
        flags = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance
        prefix, _, index = control_id.rpartition("/control/")
        for hw in self._computer.Hardware:
            for sub in hw.SubHardware:
                if str(sub.Identifier) != prefix:
                    continue
                field = next((f for f in sub.GetType().GetFields(flags) if "superio" in f.Name.lower()), None)
                if field is not None:
                    return field.GetValue(sub), int(index), flags
        raise KeyError(f"no SuperIO chip behind {control_id}")

    def saved_default(self, control_id: str) -> Optional[Dict[str, int]]:
        """The BIOS mode/PWM the chip captured for this header when it was
        first taken, or None if it has not captured one."""
        with self._lock:
            chip, i, flags = self._chip(control_id)
            t = chip.GetType()
            required = t.GetField("_restoreDefaultFanControlRequired", flags).GetValue(chip)
            if not required[i]:
                return None
            return {"mode": int(t.GetField("_initialFanControlMode", flags).GetValue(chip)[i]),
                    "pwm": int(t.GetField("_initialFanPwmCommand", flags).GetValue(chip)[i])}

    def restore_saved_default(self, control_id: str, mode: int, pwm: int) -> None:
        """Give the chip back the BIOS values a crashed run captured, then let
        it restore them the same way a clean SetDefault() would."""
        from System import Byte, Int32
        with self._lock:
            chip, i, flags = self._chip(control_id)
            t = chip.GetType()
            t.GetField("_initialFanControlMode", flags).GetValue(chip)[i] = Byte(mode)
            t.GetField("_initialFanPwmCommand", flags).GetValue(chip)[i] = Byte(pwm)
            t.GetField("_restoreDefaultFanControlRequired", flags).GetValue(chip)[i] = True
            restore = next(m for m in t.GetMethods(flags) if m.Name == "RestoreDefaultFanControl")
            restore.Invoke(chip, [Int32(i)])
        self._touched.discard(control_id)

    def close(self) -> None:
        self.release_all()
        if self._computer is not None:
            with self._lock:
                self._computer.Close()
            self._computer = None
