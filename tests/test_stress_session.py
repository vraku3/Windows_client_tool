import json
import threading
import time

import pytest

from modules.stress_test.engine.sensors import Reading, SensorAdapter
from modules.stress_test.engine.session import StressSession, SessionConfig
from modules.stress_test.engine.safety import SafetyPolicy
from modules.thermal_control.engine.model import Sensor


class FakeSensors:
    def read(self):
        return [Reading('cpu', 'CPU package', 'cpu_temp', 50, 'C')]

    def close(self):
        self.closed = True


def test_short_session_report_and_verdict(tmp_path):
    s = StressSession(SessionConfig(cpu=True, threads=1, policy=SafetyPolicy(duration_s=1)),
                      FakeSensors(), report_dir=tmp_path)
    result = s.run()
    assert result['verdict'] == 'PASSED'
    assert sum(result['cpu']['iterations']) > 0
    report = json.loads(s.json_path.read_text(encoding='utf-8'))
    assert report['verdict'] == 'PASSED'
    assert s.html_path.exists()
    assert not s.running
    assert not any(t.is_alive() for t in s.cpu.threads)


def test_initial_overheat_starts_no_load(tmp_path):
    class Hot(FakeSensors):
        def read(self):
            return [Reading('cpu', 'CPU <package>', 'cpu_temp', 95, 'C')]
    s = StressSession(SessionConfig(threads=1), Hot(), report_dir=tmp_path)
    result = s.run()
    assert result['verdict'] == 'STOPPED BY SAFETY'
    assert s.cpu is None
    assert '&lt;package&gt;' in s.html_path.read_text(encoding='utf-8')


def test_sensor_stall_cannot_block_duration_or_stop(tmp_path):
    release = threading.Event()
    class Stalled(FakeSensors):
        count = 0
        def read(self):
            self.count += 1
            if self.count > 1:
                release.wait(2)
            return super().read()
    s = StressSession(SessionConfig(threads=1, policy=SafetyPolicy(duration_s=.2)),
                      Stalled(), report_dir=tmp_path, sample_interval=.02)
    started = time.monotonic()
    try:
        assert s.run()['verdict'] == 'PASSED'
        assert time.monotonic() - started < 1
    finally:
        release.set()


def test_user_stop_joins_loads(tmp_path):
    s = StressSession(SessionConfig(threads=1), FakeSensors(), report_dir=tmp_path)
    t = threading.Thread(target=s.run)
    t.start()
    deadline = time.monotonic() + 1
    while s.cpu is None and time.monotonic() < deadline:
        time.sleep(.005)
    s.stop()
    t.join(1)
    assert not t.is_alive()
    assert s.snapshot()['verdict'] == 'STOPPED BY USER'


def test_adapter_uses_service_and_filters_limits_and_refusals():
    class Service:
        reason = 'needs the PawnIO driver and administrator rights'
        def read_now(self):
            return [Sensor('/amdcpu/0/temp/0', 'AMD Ryzen', 'Core (Tctl/Tdie)', 'temperature', 0, 'lhm'),
                    Sensor('/nvme/0/temp/1', 'NVMe', 'Critical Temperature', 'temperature', 84, 'lhm'),
                    Sensor('/gpu/0/temp/0', 'GPU', 'GPU core', 'temperature', 51, 'd3dkmt')]
    a = SensorAdapter(lambda: Service(), extra_metrics=False)
    values = a.read()
    assert not any('Critical' in r.name for r in values)
    assert next(r for r in values if r.kind == 'cpu_temp').value is None
    assert next(r for r in values if r.kind == 'gpu_temp').value == 51
    assert any('PawnIO' in r.reason for r in values)
    a.close()


def test_disappeared_sensor_is_kept_as_missing():
    class Service:
        count = 0
        reason = ''
        def read_now(self):
            self.count += 1
            return [Sensor('/amdcpu/0/temp/0', 'CPU', 'package', 'temperature', 50, 'lhm')] if self.count == 1 else []
    service = Service()
    adapter = SensorAdapter(lambda: service, extra_metrics=False)
    first = adapter.read()
    second = adapter.read()
    cpu_key = next(r.key for r in first if r.value is not None)
    lost = next(r for r in second if r.key == cpu_key)
    assert lost.value is None
    assert lost.reason


def test_ram_thread_start_failure_does_not_skip_report_or_completion(tmp_path, monkeypatch):
    from modules.stress_test.engine.ram_test import RamTest
    class CannotStart(RamTest):
        def start(self):
            self.thread = threading.Thread(target=lambda: None)
            raise RuntimeError('thread creation refused')
    s = StressSession(SessionConfig(cpu=False, ram=True, ram_bytes=128), FakeSensors(),
                      ram_factory=CannotStart, report_dir=tmp_path)
    result = s.run()
    assert result['verdict'] == 'FAILED'
    assert s.done.is_set()
    assert s.html_path.exists()


def test_computation_errors_are_failed_even_when_user_stops(tmp_path):
    from modules.stress_test.engine.cpu_load import CpuLoad
    def broken(threads, stop):
        return CpuLoad(1, stop, operations={'bad': lambda data: b'ok'}, transform=lambda n, b: b'error')
    s = StressSession(SessionConfig(threads=1), FakeSensors(), cpu_factory=broken, report_dir=tmp_path)
    thread = threading.Thread(target=s.run)
    thread.start()
    until = time.monotonic() + 1
    while not s.snapshot()['cpu'].get('error_count') and time.monotonic() < until:
        time.sleep(.005)
    s.stop()
    thread.join(1)
    assert s.snapshot()['verdict'] == 'FAILED'


def test_lost_sensor_stops_independently_of_blocked_reader(tmp_path):
    release = threading.Event()
    class Reader(FakeSensors):
        count = 0
        def read(self):
            self.count += 1
            if self.count > 1:
                release.wait(1)
            return super().read()
    s = StressSession(SessionConfig(threads=1, policy=SafetyPolicy(duration_s=1, lost_after_s=.08)),
                      Reader(), report_dir=tmp_path, sample_interval=.01)
    try:
        result = s.run()
        assert result['verdict'] == 'STOPPED BY SAFETY'
        assert 'stopped answering' in result['reason']
        assert result['readings'][0]['value'] is None
    finally:
        release.set()


def test_cpu_thread_crash_is_failed_and_load_stopped(tmp_path):
    from modules.stress_test.engine.cpu_load import CpuLoad
    def broken(threads, stop):
        calls = 0
        def operation(data):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError('injected CPU failure')
            return b'known answer'
        return CpuLoad(1, stop, operations={'crash': operation})
    s = StressSession(SessionConfig(threads=1), FakeSensors(), report_dir=tmp_path, cpu_factory=broken)
    result = s.run()
    assert result['verdict'] == 'FAILED'
    assert 'injected CPU failure' in result['reason']
    assert not any(t.is_alive() for t in s.cpu.threads)


def test_report_write_failure_is_visible(tmp_path, monkeypatch):
    import modules.stress_test.engine.session as engine
    def refused(*args):
        raise PermissionError('report folder refused')
    monkeypatch.setattr(engine, 'write_report', refused)
    s = StressSession(SessionConfig(threads=1, policy=SafetyPolicy(duration_s=.05)), FakeSensors(), report_dir=tmp_path)
    assert s.run()['verdict'] == 'FAILED'
    assert 'report could not be saved' in s.snapshot()['reason']
    assert s.done.is_set()
    assert not s.running


def test_no_cpu_temperature_gate_prevents_threads(tmp_path):
    class NoCPU(FakeSensors):
        def read(self):
            return [Reading('cpu', 'CPU', 'cpu_temp', None, 'C', 'needs PawnIO')]
    s = StressSession(SessionConfig(threads=1), NoCPU(), report_dir=tmp_path)
    assert s.run()['verdict'] == 'FAILED'
    assert 'acknowledgement' in s.snapshot()['reason']
    assert s.cpu is None


def test_ram_shortfall_cannot_pass(tmp_path):
    from types import SimpleNamespace
    from modules.stress_test.engine.ram_test import RamTest
    def factory(size, stop):
        return RamTest(size, stop, chunk_size=64, slice_size=32,
            memory_reader=lambda: SimpleNamespace(total=16 * 1024**3, available=1))
    s = StressSession(SessionConfig(cpu=False, ram=True, ram_bytes=128,
        policy=SafetyPolicy(duration_s=.05)), FakeSensors(), report_dir=tmp_path, ram_factory=factory)
    result = s.run()
    assert result['verdict'] == 'FAILED'
    assert result['ram']['allocated_bytes'] == 0
    assert result['ram']['allocation_reason']


def test_effective_clock_uses_same_nominal_reference_as_dashboard(monkeypatch):
    from types import SimpleNamespace
    from modules.dashboard import power, energy
    from modules.stress_test.engine import sensors
    monkeypatch.setattr(sensors.gpu_kmt, 'read_gpus', lambda: [])
    monkeypatch.setattr(sensors.os, 'cpu_count', lambda: 1)
    monkeypatch.setattr(power, 'read_frequencies', lambda: [SimpleNamespace(current_mhz=1000, max_mhz=4300)])
    class Counter:
        def __init__(self, count):
            assert count == 1
        def read(self, nominal):
            return [nominal[0] * 1.2]
        def close(self):
            self.closed = True
    class Energy:
        def read(self):
            return SimpleNamespace(package_w=None)
        def close(self):
            self.closed = True
    monkeypatch.setattr(power, 'EffectiveFreq', Counter)
    monkeypatch.setattr(energy, 'EnergyMeter', Energy)
    adapter = SensorAdapter()
    readings = adapter.read()
    assert next(r for r in readings if r.kind == 'cpu_clock').value == 5160
    assert next(r for r in readings if r.kind == 'package_power').value is None
    adapter.close()
