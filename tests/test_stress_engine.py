import threading
import time
from types import SimpleNamespace

import pytest

from modules.stress_test.engine.safety import SafetyPolicy, SafetyMonitor
from modules.stress_test.engine.sensors import Reading
from modules.stress_test.engine.cpu_load import CpuLoad
from modules.stress_test.engine.ram_test import RamTest, default_ram_bytes, PATTERNS


def reading(key='cpu', value=50, kind='cpu_temp'):
    return Reading(key, key, kind, value, 'C')


def test_no_temperature_requires_ack_and_caps_duration():
    with pytest.raises(ValueError, match='acknowledg'):
        SafetyMonitor(SafetyPolicy(duration_s=900), [], 0)
    monitor = SafetyMonitor(SafetyPolicy(duration_s=900, acknowledge_no_cpu=True), [], 0)
    assert monitor.duration_s == 600
    assert monitor.check([], 600).code == 'duration'


@pytest.mark.parametrize('kind,limit', [('cpu_temp', 90), ('gpu_temp', 90), ('gpu_hotspot', 105)])
def test_temperature_boundary(kind, limit):
    initial = [reading(kind=kind), reading('real_cpu')]
    m = SafetyMonitor(SafetyPolicy(), initial, 0)
    assert m.check([reading(value=limit - .01, kind=kind), reading('real_cpu')], 1) is None
    assert m.check([reading(value=limit, kind=kind), reading('real_cpu')], 2).code == 'temperature'


def test_sensor_loss_is_per_identity_and_missing_values_are_not_zero():
    # Only temperatures the cut-off depends on can be "lost": a clock counter
    # that goes quiet must not stop the test (it can warm up for seconds).
    m = SafetyMonitor(SafetyPolicy(), [reading(), reading('clock', 4000, 'cpu_clock')], 0)
    assert m.check([reading()], 5.01) is None          # CPU fresh at 5.01, clock silent
    assert m.check([], 10.02).code == 'sensor_lost'    # CPU temperature silent > 5 s


def test_sensor_recovers_and_only_initial_readable_is_required():
    m = SafetyMonitor(SafetyPolicy(), [reading(), reading('gpu', None, 'gpu_temp')], 0)
    assert m.check([reading(value=None)], 4) is None
    assert m.check([reading()], 4.5) is None
    assert m.check([reading(value=None)], 9) is None
    assert m.check([], 9.51).code == 'sensor_lost'


@pytest.mark.parametrize('limit', [69, 96, float('nan')])
def test_cpu_limits_validated(limit):
    with pytest.raises(ValueError):
        SafetyPolicy(cpu_limit=limit)


def test_cpu_checks_known_answer_and_stops():
    stop = threading.Event()
    cpu = CpuLoad(1, stop, operations={'bad': lambda data: b'correct'},
                  transform=lambda name, output: b'wrong')
    cpu.start()
    deadline = time.monotonic() + .5
    while not cpu.snapshot()['errors'] and time.monotonic() < deadline:
        time.sleep(.005)
    stop.set()
    cpu.join()
    stats = cpu.snapshot()
    assert stats['error_count'] > 0
    assert stats['errors'][0]['workload'] == 'bad'
    assert stats['errors'][0]['expected'] != stats['errors'][0]['got']
    assert not any(t.is_alive() for t in cpu.threads)


def memory(available=8 * 1024**3):
    return SimpleNamespace(total=16 * 1024**3, available=available)


def test_ram_default_preserves_reserve():
    assert default_ram_bytes(memory()) == int(.7 * memory().available)
    assert default_ram_bytes(memory(1024**3)) == 0


def test_ram_corruption_offset_and_free():
    stop = threading.Event()
    def corrupt(chunk, index, pattern):
        chunk[37] ^= 1
    ram = RamTest(128, stop, chunk_size=128, slice_size=64,
                  memory_reader=memory, after_write=corrupt)
    ram.allocate()
    ram.run_pass()
    assert ram.snapshot()['errors'][0]['offset'] == 37
    assert ram.snapshot()['errors'][0]['chunk'] == 0
    assert {e['pattern'] for e in ram.snapshot()['errors']} == set(PATTERNS)
    ram.release()
    assert not ram.chunks


def test_ram_rechecks_available_before_each_chunk():
    calls = iter([memory(), memory(1)])
    ram = RamTest(256, threading.Event(), chunk_size=128, memory_reader=lambda: next(calls))
    ram.allocate()
    assert len(ram.chunks) == 1
    assert ram.snapshot()['allocation_reason']
    ram.release()


def test_ram_patterns_healthy_and_stop_releases_memory():
    stop = threading.Event()
    ram = RamTest(256, stop, chunk_size=128, slice_size=33, memory_reader=memory)
    ram.allocate()
    ram.run_pass()
    assert ram.snapshot()['passes'] == 1
    assert ram.snapshot()['tested_bytes'] == 256
    assert ram.snapshot()['error_count'] == 0
    ram.release()
    ram.start()
    stop.set()
    ram.join()
    assert not ram.chunks


def test_walking_ones_exercises_each_bit_at_each_location():
    from modules.stress_test.engine.ram_test import pattern_bytes
    walking = [name for name in PATTERNS if name.startswith('walking ones')]
    for offset in range(16):
        assert {pattern_bytes(name, 16)[offset] for name in walking} == {1 << bit for bit in range(8)}


def test_a_quiet_clock_counter_is_not_a_lost_safety_sensor():
    from modules.stress_test.engine.safety import SafetyMonitor, SafetyPolicy
    from modules.stress_test.engine.sensors import Reading
    cpu = Reading('cpu:t', 'CPU', 'cpu_temp', 60.0, 'C')
    clock = Reading('cpu:clock', 'CPU effective clock (mean)', 'cpu_clock', 4300.0, 'MHz')
    monitor = SafetyMonitor(SafetyPolicy(duration_s=60), [cpu, clock], 0.0)
    quiet = Reading('cpu:clock', 'CPU effective clock (mean)', 'cpu_clock', None, 'MHz', 'warming up')
    assert monitor.check([cpu, quiet], 10.0) is None          # clock silent 10 s: no stop
    lost = Reading('cpu:t', 'CPU', 'cpu_temp', None, 'C', 'gone')
    assert monitor.check([lost, quiet], 16.0).code == 'sensor_lost'   # temperature silent > 5 s: stop
