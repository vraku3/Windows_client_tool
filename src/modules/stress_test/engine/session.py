"""One session, one stop Event, independent sensor pump and safety watchdog."""
import atexit
from collections import deque
from dataclasses import asdict, dataclass, field
import logging
import os
import queue
import threading
import time

from .cpu_load import CpuLoad
from .ram_test import RamTest
from .report import write_report
from .safety import SafetyMonitor, SafetyPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionConfig:
    cpu: bool = True
    ram: bool = False
    threads: int = field(default_factory=lambda: os.cpu_count() or 1)
    ram_bytes: int = 0
    policy: SafetyPolicy = field(default_factory=SafetyPolicy)

    def __post_init__(self):
        if not self.cpu and not self.ram:
            raise ValueError('Select CPU, RAM, or both')
        if self.ram and self.ram_bytes <= 0:
            raise ValueError('Choose a positive RAM size')
        if not 1 <= self.threads <= (os.cpu_count() or 1):
            raise ValueError('Invalid CPU thread count')


class StressSession:
    def __init__(self, config, sensors, *, report_dir=None, on_sample=None,
                 sample_interval=1, cpu_factory=CpuLoad, ram_factory=RamTest):
        self.config, self.sensors = config, sensors
        self.report_dir, self.on_sample = report_dir, on_sample
        self.sample_interval = sample_interval
        self.cpu_factory, self.ram_factory = cpu_factory, ram_factory
        self.stop_event = threading.Event()
        self.done = threading.Event()
        self.cpu = self.ram = None
        self.json_path = self.html_path = None
        self._lock = threading.RLock()
        self._started = None
        self._last_sample_at = None
        self._used = False
        self._user_stop = False
        self._running = False
        self._state = {'verdict': 'READY', 'reason': '', 'elapsed_s': 0, 'remaining_s': 0,
                       'readings': [], 'cpu': {}, 'ram': {}, 'iterations_per_s': 0}
        self._samples = deque(maxlen=3601)
        self._sampler = None
        self._hook = self.stop

    @property
    def running(self):
        with self._lock:
            return self._running

    def stop(self):
        with self._lock:
            self._user_stop = True
        self.stop_event.set()

    def snapshot(self):
        with self._lock:
            return {**self._state, 'readings': list(self._state['readings']),
                    'cpu': dict(self._state['cpu']), 'ram': dict(self._state['ram'])}

    def _pump(self, mailbox):
        try:
            while not self.stop_event.is_set():
                try:
                    readings = self.sensors.read()
                    mailbox.put((time.monotonic(), readings, ''), block=False)
                except queue.Full:
                    logger.warning('Stress sensor sample dropped: consumer is behind')
                except Exception as exc:
                    logger.warning('Stress sensor adapter failed: %s', exc)
                    try:
                        mailbox.put((time.monotonic(), [], str(exc)), block=False)
                    except queue.Full:
                        logger.warning('Stress sensor error mailbox full')
                if self.stop_event.wait(self.sample_interval):
                    break
        finally:
            try:
                self.sensors.close()
            except Exception as exc:
                logger.warning('Closing stress sensors failed: %s', exc)

    def _publish(self, readings, duration, now):
        cpu = self.cpu.snapshot() if self.cpu else {}
        ram = self.ram.snapshot() if self.ram else {}
        elapsed = now - self._started
        stale = self._last_sample_at is not None and now - self._last_sample_at > self.config.policy.lost_after_s
        displayed = [dict(asdict(r), value=None, reason='sensor sampling stopped answering') if stale
                     else asdict(r) for r in readings]
        with self._lock:
            self._state.update(elapsed_s=elapsed, remaining_s=max(0, duration - elapsed),
                readings=displayed, cpu=cpu, ram=ram,
                iterations_per_s=sum(cpu.get('iterations', [])) / max(.001, elapsed))

    def _final_verdict(self, state, verdict, reason):
        """Errors and missing coverage override how the run ended."""
        failures = state['cpu'].get('failures', []) + state['ram'].get('failures', [])
        errors = state['cpu'].get('error_count', 0) + state['ram'].get('error_count', 0)
        if failures:
            return 'FAILED', '; '.join(failures)
        if errors and verdict != 'STOPPED BY SAFETY':
            return 'FAILED', f'{errors} computation or memory error(s) detected; {reason}'
        if verdict != 'PASSED':
            return verdict, reason
        if self.config.cpu and not sum(state['cpu'].get('iterations', [])):
            return 'FAILED', 'No CPU operations completed'
        if self.config.ram and state['ram'].get('allocated_bytes', 0) < self.config.ram_bytes:
            return 'FAILED', 'Requested RAM coverage was not allocated; see actual coverage and reserve guard'
        if self.config.ram and not state['ram'].get('passes'):
            # The memory WAS allocated and no error was found -- the run was
            # simply too short for one full pass of all patterns. Not a failure.
            return 'INCOMPLETE', ('No errors, but no complete RAM pattern pass finished in the chosen '
                                  'time; run longer or test less RAM for a full pass')
        return verdict, reason

    def run(self):
        with self._lock:
            if self._used:
                raise RuntimeError('A stress session can only run once')
            self._used = True
            self._running = True
            self._state.update(verdict='STARTING', reason='Reading sensors before starting load')
        atexit.register(self._hook)  # Event only; never reaches Qt at interpreter shutdown.
        verdict, reason = 'FAILED', 'Session did not complete'
        readings = []
        monitor = None
        self._started = time.monotonic()
        mailbox = queue.Queue(maxsize=2)
        try:
            self._sampler = threading.Thread(target=self._pump, args=(mailbox,), name='StressSensors', daemon=True)
            self._sampler.start()
            first = None
            until = time.monotonic() + 5
            while not self.stop_event.is_set() and time.monotonic() < until:
                try:
                    first = mailbox.get(timeout=.05)
                    break
                except queue.Empty:
                    logger.debug('Stress: waiting for the first sensor sample')
                    continue
            if self.stop_event.is_set():
                verdict, reason = 'STOPPED BY USER', 'Stopped before load started'
            else:
                if first:
                    self._last_sample_at, readings, error = first
                    if error:
                        raise RuntimeError(f'Initial sensor read failed: {error}')
                self._started = time.monotonic()
                monitor = SafetyMonitor(self.config.policy, readings, self._started)
                decision = monitor.check(readings, self._started)
                self._publish(readings, monitor.duration_s, self._started)
                self._samples.append({'elapsed_s': 0, 'readings': [asdict(r) for r in readings]})
                if decision:
                    verdict, reason = 'STOPPED BY SAFETY', decision.detail
                else:
                    if self.config.cpu:
                        self.cpu = self.cpu_factory(self.config.threads, self.stop_event)
                        self.cpu.start()
                    if self.config.ram and not self.stop_event.is_set():
                        self.ram = self.ram_factory(self.config.ram_bytes, self.stop_event)
                        self.ram.start()
                    with self._lock:
                        self._state.update(verdict='RUNNING', reason='Running; continues when you leave this tab')
                    while not self.stop_event.is_set():
                        now = time.monotonic()
                        # Cached values NEVER renew the last-seen timestamps.
                        decision = monitor.check(readings, now, fresh=False)
                        try:
                            stamp, readings, error = mailbox.get_nowait()
                            self._last_sample_at = stamp
                            if error:
                                logger.warning('Stress sample unavailable: %s', error)
                            # Use capture time rather than consumption time for freshness.
                            fresh_decision = monitor.check(readings, stamp)
                            decision = decision or fresh_decision
                            self._samples.append({'elapsed_s': stamp - self._started,
                                                  'readings': [asdict(r) for r in readings]})
                        except queue.Empty:
                            logger.debug('No new stress sensor sample')
                        self._publish(readings, monitor.duration_s, now)
                        if decision:
                            self.stop_event.set()
                            verdict = 'PASSED' if decision.code == 'duration' else 'STOPPED BY SAFETY'
                            reason = decision.detail
                            break
                        if self.on_sample:
                            self.on_sample(self.snapshot())
                        self.stop_event.wait(.05)
                    if self._user_stop:
                        verdict, reason = 'STOPPED BY USER', 'Stop requested by user or app shutdown'
        except Exception as exc:
            logger.exception('Stress session failed')
            verdict, reason = 'FAILED', str(exc)
        finally:
            self.stop_event.set()
            for load in (self.cpu, self.ram):
                if load:
                    load.join()
            # A driver may never return. Its daemon sampler owns counter cleanup;
            # shutdown never waits on it, and no new sampling jobs can accumulate.
            if self._sampler:
                self._sampler.join(timeout=.05)
            duration = monitor.duration_s if monitor else self.config.policy.duration_s
            self._publish(readings, duration, time.monotonic())
            verdict, reason = self._final_verdict(self.snapshot(), verdict, reason)
            with self._lock:
                self._state.update(verdict=verdict, reason=reason)
            result = {**self.snapshot(), 'config': asdict(self.config), 'samples': list(self._samples),
                      'started_unix': time.time() - self._state['elapsed_s'], 'effective_duration_s': duration,
                      'retained_error_limit_per_engine': 100, 'retained_sample_limit': self._samples.maxlen}
            try:
                self.json_path, self.html_path = write_report(result, self.report_dir)
            except Exception as exc:
                logger.exception('Writing stress report failed')
                with self._lock:
                    self._state.update(verdict='FAILED', reason=f'{reason}; report could not be saved: {exc}')
                result = {**result, **self.snapshot()}
            with self._lock:
                self._running = False
            atexit.unregister(self._hook)
            self.done.set()
        return result
