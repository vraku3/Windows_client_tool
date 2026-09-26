"""Rolling ping statistics: loss, min/avg/max, jitter, and an RTT history. Qt-free.

Jitter is the mean absolute difference between consecutive successful RTTs
(the RFC 3550 idea, unsmoothed), so one spike shows up as a spike.
A lost probe is recorded as ``None`` in the history and counts toward loss;
it never contributes a 0 ms RTT.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional


class PingStats:
    def __init__(self, history: int = 300) -> None:
        self.history: Deque[Optional[float]] = deque(maxlen=history)
        self.sent = 0
        self.received = 0
        self._rtt_sum = 0.0
        self._min: Optional[float] = None
        self._max: Optional[float] = None
        self._last: Optional[float] = None
        self._jitter_sum = 0.0
        self._jitter_n = 0
        self.longest_loss_run = 0
        self._loss_run = 0

    def add(self, rtt_ms: Optional[float]) -> None:
        self.sent += 1
        self.history.append(rtt_ms)
        if rtt_ms is None:
            self._loss_run += 1
            self.longest_loss_run = max(self.longest_loss_run, self._loss_run)
            self._last = None
            return
        self._loss_run = 0
        self.received += 1
        self._rtt_sum += rtt_ms
        self._min = rtt_ms if self._min is None else min(self._min, rtt_ms)
        self._max = rtt_ms if self._max is None else max(self._max, rtt_ms)
        if self._last is not None:
            self._jitter_sum += abs(rtt_ms - self._last)
            self._jitter_n += 1
        self._last = rtt_ms

    @property
    def loss_pct(self) -> Optional[float]:
        return None if self.sent == 0 else 100.0 * (self.sent - self.received) / self.sent

    @property
    def avg(self) -> Optional[float]:
        return None if self.received == 0 else self._rtt_sum / self.received

    @property
    def min(self) -> Optional[float]:
        return self._min

    @property
    def max(self) -> Optional[float]:
        return self._max

    @property
    def jitter(self) -> Optional[float]:
        return None if self._jitter_n == 0 else self._jitter_sum / self._jitter_n

    def summary(self) -> str:
        if self.sent == 0:
            return "No probes yet"
        def f(v: Optional[float]) -> str:
            return "-" if v is None else f"{v:.1f}"
        return (f"sent {self.sent}  received {self.received}  loss {self.loss_pct:.1f}%  "
                f"min {f(self.min)}  avg {f(self.avg)}  max {f(self.max)}  jitter {f(self.jitter)} ms  "
                f"longest loss run {self.longest_loss_run}")
