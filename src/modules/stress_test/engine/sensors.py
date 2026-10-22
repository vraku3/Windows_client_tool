"""Read-only adapter over the existing ThermalService and dashboard counters.

The service getter is supplied by Qt-side code; this module never imports Qt or
opens LibreHardwareMonitor. Read/close on one sampling thread, not the UI thread.
"""
from dataclasses import dataclass, replace
import logging
import math
import os

from modules.thermal_control.engine import gpu_kmt, view

logger = logging.getLogger(__name__)
CPU_REASON = 'CPU temperature needs the PawnIO driver and administrator rights; open Thermal Control to check availability'


@dataclass(frozen=True)
class Reading:
    key: str
    name: str
    kind: str
    value: float | None
    unit: str
    reason: str = ''

    def __post_init__(self):
        if self.value is not None and (not math.isfinite(self.value) or
                                      (self.kind.endswith('temp') or self.kind == 'gpu_hotspot') and self.value <= 0):
            object.__setattr__(self, 'value', None)
            object.__setattr__(self, 'reason', self.reason or 'sensor refused a valid reading')


def temperature_kind(sensor):
    text = f'{sensor.id} {sensor.hardware} {sensor.name}'.lower()
    if any(word in text for word in ('gpu', 'radeon', 'nvidia', 'geforce')):
        return 'gpu_hotspot' if any(w in text for w in ('hotspot', 'hot spot', 'junction')) else 'gpu_temp'
    if any(word in text for word in ('/amdcpu', '/intelcpu', 'ryzen', 'intel core', 'cpu', 'tctl', 'tdie')):
        return 'cpu_temp'
    return 'other_temp'


class SensorAdapter:
    def __init__(self, service_getter=lambda: None, *, extra_metrics=True):
        self.get_service = service_getter
        self.extra_metrics = extra_metrics
        self._initialized = False
        self._clock = self._energy = None
        self._nominal = []
        self._clock_reason = 'effective-clock counters unavailable'
        self._power_reason = 'Energy Meter package counters unavailable on this PC'
        self._known = {}

    def _init_metrics(self):
        self._initialized = True
        from modules.dashboard.power import EffectiveFreq, read_frequencies
        from modules.dashboard.energy import EnergyMeter
        try:
            frequencies = read_frequencies()
            # Existing EffectiveFreq supports processor group 0 (at most 64).
            if (os.cpu_count() or 1) > 64:
                raise OSError('effective clock unavailable across more than 64 logical processors')
            if not frequencies or any(f.max_mhz <= 0 for f in frequencies):
                raise OSError('Windows nominal CPU clock could not be read')
            self._nominal = [f.max_mhz for f in frequencies]
            self._clock = EffectiveFreq(len(self._nominal))
        except (OSError, RuntimeError, AttributeError) as exc:
            self._clock_reason = str(exc)
            logger.warning('Stress effective-clock source unavailable: %s', exc)
        try:
            self._energy = EnergyMeter()
        except (OSError, RuntimeError, AttributeError) as exc:
            self._power_reason = str(exc)
            logger.info('Stress package-power source unavailable: %s', exc)

    def read(self):
        reason = CPU_REASON
        try:
            service = self.get_service()
            if service is not None:
                reason = getattr(service, 'reason', '') or CPU_REASON
                sensors = service.read_now()
            else:
                sensors = gpu_kmt.read_gpus()
        except Exception as exc:
            logger.warning('Stress thermal read failed: %s', exc)
            sensors = []
            reason = f'thermal read failed: {exc}'
        result = []
        for sensor in sensors:
            if sensor.kind != 'temperature' or view.is_limit(sensor):
                continue
            kind = temperature_kind(sensor)
            result.append(Reading(sensor.id, f'{sensor.hardware}: {sensor.name}', kind, sensor.value, 'C',
                                  (reason if kind == 'cpu_temp' else 'sensor did not answer')
                                  if sensor.value is None or sensor.value <= 0 else ''))
        if not any(r.kind == 'cpu_temp' for r in result):
            result.append(Reading('missing:cpu', 'CPU temperature', 'cpu_temp', None, 'C', reason))
        if not any(r.kind == 'gpu_temp' for r in result):
            result.append(Reading('missing:gpu', 'GPU edge temperature', 'gpu_temp', None, 'C',
                                  'the GPU driver did not expose a temperature'))
        if self.extra_metrics:
            result.extend(self._metrics())
        present = {r.key for r in result}
        for key, previous in self._known.items():
            if key not in present:
                result.append(replace(previous, value=None, reason='sensor disappeared from the thermal source'))
        self._known = {r.key: r for r in result if not r.key.startswith('missing:')}
        return result

    def _metrics(self):
        if not self._initialized:
            self._init_metrics()
        clock = power = None
        if self._clock:
            try:
                values = self._clock.read(self._nominal)
                if values and all(v > 0 for v in values):
                    clock = sum(values) / len(values)
                else:
                    self._clock_reason = 'effective-clock rate counter is warming up or did not answer'
            except Exception as exc:
                logger.warning('Stress CPU clock read failed: %s', exc)
                self._clock_reason = str(exc)
        if self._energy:
            try:
                energy = self._energy.read()
                power = energy.package_w if energy else None
            except Exception as exc:
                logger.warning('Stress package power read failed: %s', exc)
                self._power_reason = str(exc)
        return [Reading('cpu:clock', 'CPU effective clock (mean)', 'cpu_clock', clock, 'MHz',
                        self._clock_reason if clock is None else ''),
                Reading('cpu:power', 'CPU package power', 'package_power', power, 'W',
                        self._power_reason if power is None else '')]

    def close(self):
        for source in (self._clock, self._energy):
            if source is not None:
                try:
                    source.close()
                except Exception as exc:
                    logger.warning('Closing stress sensor counter failed: %s', exc)
        self._clock = self._energy = None
