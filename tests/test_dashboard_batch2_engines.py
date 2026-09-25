"""Topology, power/frequency and PID-recycling logic (no Qt)."""
import struct
from types import SimpleNamespace

from modules.dashboard import power as pw
from modules.dashboard import topology as tp
from modules.dashboard.recycle_watch import RecycleWatch


def _core_record(efficiency, mask, group=0):
    body = bytes([1, efficiency]) + bytes(20) + struct.pack("<H", 1)
    body += struct.pack("<QH3H", mask, group, 0, 0, 0)
    return struct.pack("<II", tp.RELATION_PROCESSOR_CORE, 8 + len(body)) + body


def _numa_record(node, mask, group=0):
    body = struct.pack("<I", node) + bytes(18) + struct.pack("<H", 1)
    body += struct.pack("<QH3H", mask, group, 0, 0, 0)
    return struct.pack("<II", tp.RELATION_NUMA_NODE, 8 + len(body)) + body


def test_a_hybrid_layout_is_classified_and_labelled():
    # two P cores (SMT pairs, class 1) and two E cores (single thread, class 0)
    buf = (_core_record(1, 0b11) + _core_record(1, 0b1100)
           + _core_record(0, 0b10000) + _core_record(0, 0b100000) + _numa_record(0, 0b111111))
    topo = tp.parse(buf)
    assert len(topo.cores) == 4 and topo.is_hybrid
    assert [topo.kind_of(i) for i in range(6)] == ["P", "P", "P", "P", "E", "E"]
    assert "2 performance + 2 efficiency" in topo.summary() and "1 NUMA node" in topo.summary()
    assert topo.cores[0].threads == [0, 1]


def test_identical_cores_are_not_labelled_and_two_numa_nodes_are_counted():
    buf = _core_record(0, 0b1) + _core_record(0, 0b10) + _numa_record(0, 0b1) + _numa_record(1, 0b10)
    topo = tp.parse(buf)
    assert not topo.is_hybrid and topo.kind_of(0) == ""
    assert "all cores identical" in topo.summary() and "2 NUMA nodes" in topo.summary()
    assert topo.cores[1].numa_node == 1


def test_a_corrupt_record_size_stops_the_walk_instead_of_reading_garbage():
    good = _core_record(0, 0b1)
    bad = struct.pack("<II", 0, 4)            # size smaller than its own header
    assert len(tp.parse(good + bad).cores) == 1
    assert tp.parse(b"").cores == []


def test_real_topology_matches_psutil():
    import psutil
    topo = tp.read_topology()
    assert topo is not None
    assert sum(len(c.threads) for c in topo.cores) == psutil.cpu_count(logical=True)


def test_real_frequencies_read_and_summarise():
    freqs = pw.read_frequencies()
    assert freqs and all(f.max_mhz >= 0 for f in freqs)
    assert "MHz" in pw.summarise(freqs)


def test_throttling_is_a_limit_below_max():
    assert pw.CoreFreq(0, 3000, 4500, 3000).throttled
    assert not pw.CoreFreq(0, 3000, 4500, 4500).throttled
    assert not pw.CoreFreq(0, 3000, 4500, 0).throttled      # 0 = no limit reported
    assert pw.CoreFreq(0, 2250, 4500, 4500).percent == 50.0


SAMPLE = """Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance) *
"""


def test_power_plans_parse_and_the_active_one_is_marked():
    plans = pw.parse_plans(SAMPLE)
    assert [p.name for p in plans] == ["Balanced", "High performance"]
    assert [p.active for p in plans] == [False, True]


def test_setting_a_plan_validates_and_verifies(monkeypatch):
    assert pw.set_active_plan("not-a-guid")[0] is False
    monkeypatch.setattr(pw, "_powercfg", lambda *a: "" if a[0] == "/setactive" else SAMPLE)
    ok, msg = pw.set_active_plan("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    assert ok
    ok, msg = pw.set_active_plan("381b4222-f694-41f0-9685-ff5bb260df2e")
    assert not ok and "did not change" in msg          # exit 0 is not proof


def test_power_status_reads_this_machine():
    status = pw.read_power_status()
    assert status.on_battery in (True, False, None)


def _snap(*procs, taken=1.0):
    by_pid = {pid: SimpleNamespace(pid=pid, name=name, raw=SimpleNamespace(create_time=created))
              for pid, name, created in procs}
    return SimpleNamespace(by_pid=by_pid, taken_at=taken)


def test_a_reused_pid_is_reported_once_with_both_names():
    watch = RecycleWatch()
    assert watch.update(_snap((100, "a.exe", 1000), (5, "sys", 0))) == []
    assert watch.update(_snap((100, "a.exe", 1000))) == []              # same process: nothing
    events = watch.update(_snap((100, "b.exe", 2000), taken=9.0))       # same PID, new creation
    assert len(events) == 1 and events[0].old_name == "a.exe" and events[0].new_name == "b.exe"
    assert watch.update(_snap((100, "b.exe", 2000))) == []              # not reported again
    assert watch.total == 1 and watch.recently_reused(100, 60, now=20.0)
    assert not watch.recently_reused(100, 5, now=20.0)


def test_system_pids_are_never_flagged():
    watch = RecycleWatch()
    watch.update(_snap((4, "System", 1)))
    assert watch.update(_snap((4, "System", 2))) == []


def test_effective_frequency_reads_a_plausible_clock_after_priming():
    import time
    freqs = pw.read_frequencies()
    eff = pw.EffectiveFreq(len(freqs))
    try:
        assert eff.read([f.max_mhz for f in freqs]) is None          # first sample only primes
        time.sleep(1.1)
        out = eff.read([f.max_mhz for f in freqs])
    finally:
        eff.close()
    assert out is not None and len(out) == len(freqs)
    assert all(500 <= mhz <= 8000 for mhz in out), out
