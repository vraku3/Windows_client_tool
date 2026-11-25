"""NVIDIA GPU telemetry and fan control through NVML (nvml.dll). No Qt.

Loaded lazily via ctypes from System32 or NVIDIA Corporation\\NVSMI; returns
empty results cleanly on systems without an NVIDIA GPU/driver.
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import c_char_p, c_uint, c_void_p, byref
from dataclasses import dataclass
from typing import List, Optional

from .model import FAN, TEMPERATURE, Sensor

logger = logging.getLogger(__name__)

_NVML_TEMPERATURE_GPU = 0
_NVML_CLOCK_GRAPHICS = 0
_NVML_CLOCK_MEM = 2
_NVML_FAN_POLICY_MANUAL = 1
_NVML_FAN_POLICY_AUTO = 0


@dataclass
class NvmlGpu:
    index: int
    name: str
    temp_c: Optional[float]
    fan_count: int
    fan_pct: Optional[int]
    power_w: Optional[float]
    gpu_clock_mhz: Optional[int]
    mem_clock_mhz: Optional[int]


def _find_nvml() -> Optional[str]:
    win_dir = os.environ.get("WINDIR", r"C:\Windows")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        os.path.join(win_dir, "System32", "nvml.dll"),
        os.path.join(pf, "NVIDIA Corporation", "NVSMI", "nvml.dll"),
    ]
    return next((p for p in candidates if os.path.isfile(p)), None)


def _load_nvml():
    path = _find_nvml()
    if not path:
        return None
    try:
        dll = ctypes.CDLL(path)
        if dll.nvmlInit_v2() != 0:
            return None
        return dll
    except OSError as e:
        logger.debug("nvml load failed: %s", e)
        return None


def read_gpus() -> List[NvmlGpu]:
    """Read live telemetry for all NVIDIA GPUs present via NVML."""
    dll = _load_nvml()
    if dll is None:
        return []
    out: List[NvmlGpu] = []
    try:
        count = c_uint(0)
        if dll.nvmlDeviceGetCount_v2(byref(count)) != 0:
            return []
        for i in range(count.value):
            dev = c_void_p()
            if dll.nvmlDeviceGetHandleByIndex_v2(c_uint(i), byref(dev)) != 0:
                continue
            buf = ctypes.create_string_buffer(96)
            name = buf.value.decode("utf-8", errors="replace") if dll.nvmlDeviceGetName(dev, buf, 96) == 0 else f"NVIDIA GPU {i}"

            temp_v = c_uint(0)
            temp_c = float(temp_v.value) if dll.nvmlDeviceGetTemperature(dev, _NVML_TEMPERATURE_GPU, byref(temp_v)) == 0 else None

            fan_cnt_v = c_uint(0)
            fan_count = fan_cnt_v.value if hasattr(dll, "nvmlDeviceGetNumFans") and dll.nvmlDeviceGetNumFans(dev, byref(fan_cnt_v)) == 0 else 1

            fan_v = c_uint(0)
            fan_pct = int(fan_v.value) if dll.nvmlDeviceGetFanSpeed(dev, byref(fan_v)) == 0 else None

            pwr_mw = c_uint(0)
            power_w = round(pwr_mw.value / 1000.0, 1) if dll.nvmlDeviceGetPowerUsage(dev, byref(pwr_mw)) == 0 else None

            gclk = c_uint(0)
            gpu_clock_mhz = int(gclk.value) if dll.nvmlDeviceGetClockInfo(dev, _NVML_CLOCK_GRAPHICS, byref(gclk)) == 0 else None

            mclk = c_uint(0)
            mem_clock_mhz = int(mclk.value) if dll.nvmlDeviceGetClockInfo(dev, _NVML_CLOCK_MEM, byref(mclk)) == 0 else None

            out.append(NvmlGpu(
                index=i,
                name=name,
                temp_c=temp_c,
                fan_count=fan_count,
                fan_pct=fan_pct,
                power_w=power_w,
                gpu_clock_mhz=gpu_clock_mhz,
                mem_clock_mhz=mem_clock_mhz,
            ))
    except Exception as e:
        logger.debug("NVML read failed: %s", e)
    finally:
        try:
            dll.nvmlShutdown()
        except Exception:
            pass
    return out


def read_sensors() -> List[Sensor]:
    """Convert NVML telemetry into Thermal Control Sensor records."""
    sensors: List[Sensor] = []
    for g in read_gpus():
        slug = f"nvml-{g.index}"
        if g.temp_c is not None:
            sensors.append(Sensor(f"/{slug}/temperature", g.name, "GPU Core", TEMPERATURE, g.temp_c, "nvml"))
        if g.fan_pct is not None:
            sensors.append(Sensor(f"/{slug}/fan", g.name, "GPU Fan (%)", FAN, float(g.fan_pct), "nvml"))
    return sensors


def set_fan_speed(index: int, percent: int) -> bool:
    """Set manual fan speed % on all fans of NVIDIA GPU `index`."""
    dll = _load_nvml()
    if dll is None:
        return False
    try:
        dev = c_void_p()
        if dll.nvmlDeviceGetHandleByIndex_v2(c_uint(index), byref(dev)) != 0:
            return False
        cnt = c_uint(1)
        if hasattr(dll, "nvmlDeviceGetNumFans"):
            dll.nvmlDeviceGetNumFans(dev, byref(cnt))
        ok = False
        pct = max(0, min(100, int(percent)))
        for fan_idx in range(max(1, cnt.value)):
            if hasattr(dll, "nvmlDeviceSetFanSpeed_v2"):
                if dll.nvmlDeviceSetFanSpeed_v2(dev, c_uint(fan_idx), c_uint(pct)) == 0:
                    ok = True
        return ok
    except Exception as e:
        logger.debug("NVML set_fan_speed failed: %s", e)
        return False
    finally:
        try:
            dll.nvmlShutdown()
        except Exception:
            pass


def reset_fan_auto(index: int) -> bool:
    """Restore automatic fan control on NVIDIA GPU `index`."""
    dll = _load_nvml()
    if dll is None:
        return False
    try:
        dev = c_void_p()
        if dll.nvmlDeviceGetHandleByIndex_v2(c_uint(index), byref(dev)) != 0:
            return False
        cnt = c_uint(1)
        if hasattr(dll, "nvmlDeviceGetNumFans"):
            dll.nvmlDeviceGetNumFans(dev, byref(cnt))
        ok = False
        for fan_idx in range(max(1, cnt.value)):
            if hasattr(dll, "nvmlDeviceSetDefaultFanSpeed_v2"):
                if dll.nvmlDeviceSetDefaultFanSpeed_v2(dev, c_uint(fan_idx)) == 0:
                    ok = True
            elif hasattr(dll, "nvmlDeviceSetFanControlPolicy"):
                if dll.nvmlDeviceSetFanControlPolicy(dev, c_uint(fan_idx), c_uint(_NVML_FAN_POLICY_AUTO)) == 0:
                    ok = True
        return ok
    except Exception as e:
        logger.debug("NVML reset_fan_auto failed: %s", e)
        return False
    finally:
        try:
            dll.nvmlShutdown()
        except Exception:
            pass
