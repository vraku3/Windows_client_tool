"""The live readers a Flight Recorder Sampler is built from. No Qt.

Each one is the reader another tab already trusts -- the process snapshot
(Processes), the GPU engine counters (Performance), the RAPL energy meter
(Energy), the effective-clock counter (Power & Freq) -- held open for the
recording's lifetime, because each needs two readings to produce a rate.

A reader that cannot be opened, or that refuses a read, answers None, which
the recording keeps as a gap. Nothing here raises into the sampling loop.
"""
import logging
from typing import Dict, List, Optional

from .flight_recorder import ProcRow, Sampler

logger = logging.getLogger(__name__)


class LiveSources:
    def __init__(self) -> None:
        self._snapshot = self._open_snapshot()
        self._gpu = self._open("GPU counters", self._open_gpu)
        self._meter = self._open("energy meter", self._open_meter)
        self._eff, self._nominal = self._open_clock()

    # ---- opening -------------------------------------------------------------------

    @staticmethod
    def _open(what: str, opener):
        try:
            return opener()
        except Exception as e:  # each reader fails in its own way; the panel shows a gap
            logger.info("flight recorder: %s unavailable: %s", what, e)
            return None

    @staticmethod
    def _open_snapshot():
        from core.procengine.snapshot import SnapshotSource
        return SnapshotSource()

    @staticmethod
    def _open_gpu():
        from core.procengine.gpuinfo import GpuSampler
        return GpuSampler()

    @staticmethod
    def _open_meter():
        from .energy import EnergyMeter
        return EnergyMeter()

    def _open_clock(self):
        from . import power
        freqs = power.read_frequencies()
        nominal = [f.max_mhz for f in freqs] if freqs else []
        eff = self._open("clock counter", lambda: power.EffectiveFreq(len(nominal))) if nominal else None
        return eff, nominal

    # ---- readings --------------------------------------------------------------------

    def processes(self) -> Optional[List[ProcRow]]:
        if self._snapshot is None:
            return None
        try:
            snap = self._snapshot.read()
        except OSError as e:
            logger.warning("flight recorder: process snapshot failed: %s", e)
            return None
        rows = []
        for info in snap.by_pid.values():
            r = info.rates
            parts = (r.read_bps, r.write_bps, r.other_bps)
            io = None if any(p is None for p in parts) else sum(parts)
            rows.append(ProcRow(info.name, info.pid, r.cpu_percent, info.raw.working_set_private, io))
        return rows

    def gpu(self) -> Optional[float]:
        if self._gpu is None:
            return None
        adapters = self._gpu.sample()
        busy = [a.utilisation for a in (adapters or []) if a.utilisation is not None]
        return max(busy) if busy else None

    def gpu_by_pid(self) -> Optional[Dict[int, float]]:
        # Reads the collection gpu() just made; it must run after gpu().
        return self._gpu.process_usage() if self._gpu is not None else None

    def power(self) -> Optional[float]:
        if self._meter is None:
            return None
        reading = self._meter.read()
        return reading.package_w if reading is not None else None

    def clock(self) -> Optional[float]:
        if not self._eff:
            return None
        values = self._eff.read(self._nominal)
        return sum(values) / len(values) if values else None

    def sampler(self) -> Sampler:
        return Sampler(clock=self.clock, processes=self.processes, gpu=self.gpu,
                       gpu_by_pid=self.gpu_by_pid, power=self.power)

    def close(self) -> None:
        for reader in (self._gpu, self._meter, self._eff):
            if reader is not None:
                try:
                    reader.close()
                except Exception as e:  # closing a counter handle that already went away
                    logger.info("flight recorder: closing a reader failed: %s", e)
        self._gpu = self._meter = self._eff = None
