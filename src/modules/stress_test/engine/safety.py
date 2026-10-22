"""Safety decisions independent of Qt, hardware access and load threads."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SafetyPolicy:
    duration_s: float = 300
    cpu_limit: float = 90
    gpu_limit: float = 90
    hotspot_limit: float = 105
    acknowledge_no_cpu: bool = False
    lost_after_s: float = 5

    def __post_init__(self):
        if not math.isfinite(self.cpu_limit) or not 70 <= self.cpu_limit <= 95:
            raise ValueError('CPU limit must be between 70 and 95 C')
        for name, value in [('duration', self.duration_s), ('GPU limit', self.gpu_limit),
                            ('hotspot limit', self.hotspot_limit), ('sensor timeout', self.lost_after_s)]:
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if not 70 <= self.gpu_limit <= 90 or not 90 <= self.hotspot_limit <= 105:
            raise ValueError('GPU limits cannot exceed 90 C edge / 105 C hotspot')


@dataclass(frozen=True)
class StopReason:
    code: str
    detail: str


class SafetyMonitor:
    def __init__(self, policy, initial, started):
        self.policy = policy
        self.started = started
        self.duration_s = policy.duration_s
        if not any(r.kind == 'cpu_temp' and r.value is not None for r in initial):
            if not policy.acknowledge_no_cpu:
                raise ValueError('CPU temperature unavailable: explicit acknowledgement is required')
            self.duration_s = min(600, self.duration_s)
        self.limits = {'cpu_temp': policy.cpu_limit, 'gpu_temp': policy.gpu_limit,
                       'gpu_hotspot': policy.hotspot_limit}
        # Only the temperatures the cut-off depends on count as "lost": the
        # effective-clock and package-power counters can go quiet while they
        # warm up, and that must not stop a healthy test as a safety event.
        self._last_seen = {r.key: started for r in initial
                           if r.value is not None and r.kind in self.limits}
        self._names = {r.key: r.name for r in initial}

    def check(self, readings, now, *, fresh=True):
        for r in readings:
            if r.value is None or not math.isfinite(r.value):
                continue
            if fresh and r.key in self._last_seen and r.kind in self.limits:
                self._last_seen[r.key] = now
            limit = self.limits.get(r.kind)
            if limit is not None and r.value >= limit:
                return StopReason('temperature', f'{r.name}: {r.value:.1f} C >= {limit:g} C')
        for key, last in self._last_seen.items():
            if now - last > self.policy.lost_after_s:
                return StopReason('sensor_lost', f'{self._names[key]} stopped answering for more than '
                                  f'{self.policy.lost_after_s:g} s')
        if now - self.started >= self.duration_s:
            return StopReason('duration', 'Selected duration completed')
        return None
