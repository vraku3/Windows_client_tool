"""GPU engine utilisation, adapter memory, and the adapter's facts.

Two sources, split the way the process engine splits hot from cold:

- **PDH**, held open across ticks, for utilisation and memory in use. It is
  the only interface that attributes GPU work the way Task Manager does,
  and a full sample costs 0.33 ms.
- **The DirectX registry**, read once, for the facts -- name, driver
  version, feature level, and the memory limits the live figures are
  measured against.

The discipline is the same as everywhere else in the engine: a value we
could not read is `None` with a reason, never `0`.
"""
import time

import pytest

from core.procengine.gpuinfo import (
    AdapterUsage, EngineLoad, GpuSampler, adapter_facts, feature_level_name,
    parse_engine_instance, parse_memory_instance, summarise_engines,
)


def _load(engtype, eng, percent, luid="0x00000000_0x00015725", pid=100):
    """One `GPU Engine` reading, spelled the way PDH spells it.

    The pid is part of the key: PDH returns a dict, so two readings of the
    same engine only exist if they came from different processes.
    """
    return (f"pid_{pid}_luid_{luid}_phys_0_eng_{eng}_engtype_{engtype}",
            percent)


# ---- reading the instance names -----------------------------------------

def test_an_engine_instance_name_is_taken_apart():
    parsed = parse_engine_instance(
        "pid_12344_luid_0x00000000_0x00015725_phys_0_eng_2_engtype_Compute 0")
    assert parsed is not None
    assert parsed.pid == 12344
    assert parsed.luid == 0x00015725
    assert parsed.engine == 2
    assert parsed.engtype == "Compute 0"


def test_an_engine_type_may_contain_spaces_and_digits():
    """Video Codec Engine and Compute 0 are both real engine types on this
    machine; a parser that stops at the first space or digit loses them."""
    for engtype in ("3D", "Copy", "Compute 0", "Video JPEG 0",
                    "Video Codec Engine", "High Priority 3D"):
        name = f"pid_4_luid_0x00000000_0x1_phys_0_eng_0_engtype_{engtype}"
        parsed = parse_engine_instance(name)
        assert parsed is not None and parsed.engtype == engtype


def test_the_high_half_of_the_luid_is_not_dropped():
    """A LUID is 64 bits and PDH spells both halves. Keeping only the low
    half merges two adapters into one on a machine that has enough of them
    to run the counter off the bottom 32 bits."""
    parsed = parse_engine_instance(
        "pid_1_luid_0x00000001_0x00000002_phys_0_eng_0_engtype_3D")
    assert parsed.luid == (1 << 32) | 2


def test_an_unrecognised_instance_name_is_none_not_a_guess():
    assert parse_engine_instance("total") is None
    assert parse_engine_instance("") is None


def test_a_memory_instance_name_is_taken_apart():
    parsed = parse_memory_instance(
        "pid_12344_luid_0x00000000_0x00015725_phys_0")
    assert parsed is not None and parsed.luid == 0x00015725
    assert parsed.pid == 12344
    adapter = parse_memory_instance("luid_0x00000000_0x00015725_phys_0")
    assert adapter is not None and adapter.luid == 0x00015725
    assert adapter.pid is None


def test_a_partitioned_adapter_memory_instance_still_parses():
    """The local-memory counter set spells a `_part_0` suffix the others do
    not, and dropping those instances loses the adapter entirely."""
    parsed = parse_memory_instance(
        "luid_0x00000000_0x00015725_phys_0_part_0")
    assert parsed is not None and parsed.luid == 0x00015725


# ---- turning instances into a per-adapter figure ------------------------

def test_one_engines_processes_are_added_together():
    """Each instance is one process's share of one physical engine, so a
    physical engine's busy time is the sum over the processes on it."""
    engines = summarise_engines(dict([
        _load("3D", 0, 30.0, pid=100), _load("3D", 0, 20.0, pid=200)]))
    assert engines[0x00015725] == (EngineLoad(engtype="3D", percent=50.0),)


def test_engine_types_are_kept_apart():
    engines = summarise_engines(dict([
        _load("3D", 0, 40.0), _load("Copy", 1, 10.0)]))
    loads = {load.engtype: load.percent for load in engines[0x00015725]}
    assert loads == {"3D": 40.0, "Copy": 10.0}


def test_an_engine_type_on_several_physical_engines_is_not_summed():
    """The basic render driver presents THIRTY-THREE physical 3D engines,
    and this machine's real card presents two Copy engines. Adding them the
    way the processes are added yields 3300% for a class of engine that is
    at most fully busy. The class reads as its busiest member."""
    engines = summarise_engines(dict([
        _load("Copy", 1, 90.0), _load("Copy", 3, 10.0)]))
    assert engines[0x00015725] == (EngineLoad(engtype="Copy", percent=90.0),)


def test_utilisation_is_the_busiest_engine_not_the_total():
    """Task Manager's headline GPU figure is the busiest engine. Summing
    the engine types instead reports 160% for a card doing two things at
    once, neither of them flat out."""
    usage = AdapterUsage(luid=1, engines=(
        EngineLoad("3D", 60.0), EngineLoad("Copy", 90.0),
        EngineLoad("Compute 0", 10.0)))
    assert usage.utilisation == 90.0


def test_an_adapter_with_no_engine_readings_has_no_utilisation():
    """Not zero: an adapter that reported nothing is not an idle one."""
    assert AdapterUsage(luid=1, engines=()).utilisation is None


def test_engines_come_back_busiest_first():
    engines = summarise_engines(dict([
        _load("Copy", 0, 5.0), _load("3D", 1, 70.0),
        _load("Compute 0", 2, 30.0)]))
    order = [load.engtype for load in engines[0x00015725]]
    assert order == ["3D", "Compute 0", "Copy"]


def test_adapters_are_kept_apart():
    engines = summarise_engines(dict([
        _load("3D", 0, 40.0, luid="0x00000000_0x00000001"),
        _load("3D", 0, 70.0, luid="0x00000000_0x00000002")]))
    assert engines[1][0].percent == 40.0
    assert engines[2][0].percent == 70.0


def test_an_unparsable_instance_is_skipped_rather_than_crashing():
    engines = summarise_engines({"total": 5.0, **dict([_load("3D", 0, 1.0)])})
    assert list(engines) == [0x00015725]


# ---- the feature level --------------------------------------------------

def test_a_feature_level_reads_as_directx_calls_it():
    assert feature_level_name(0xC200) == "12_2"
    assert feature_level_name(0xC100) == "12_1"
    assert feature_level_name(0xB000) == "11_0"
    assert feature_level_name(0x9300) == "9_3"


def test_a_missing_feature_level_is_none():
    assert feature_level_name(None) is None
    assert feature_level_name(0) is None


# ---- the real machine ---------------------------------------------------

def test_the_machine_has_at_least_one_display_adapter():
    assert adapter_facts(), "no display adapter was found"


def test_every_adapter_is_named():
    # A real, measured shape: a DirectX registry entry can carry
    # literally no Description at all (a stale/orphaned adapter subkey,
    # distinct from software=True's known WARP/Basic-Render identity) --
    # gpuinfo.py discloses that via unavailable["name"] rather than
    # inventing a name, so a disclosed-unavailable one is not a bug here.
    for facts in adapter_facts():
        if "name" in facts.unavailable:
            continue
        assert facts.name


def test_a_real_adapter_reports_its_driver_version():
    # Same real shape as above: an adapter with no driver_version at all
    # discloses it via unavailable["driver_version"] -- distinct from a
    # hardware adapter that should have one and silently doesn't.
    real = [f for f in adapter_facts()
           if not f.software and "driver_version" not in f.unavailable]
    assert real, "no hardware adapter was found"
    for facts in real:
        assert facts.driver_version
        assert facts.driver_version.count(".") == 3


def test_the_rendering_adapter_reports_a_directx_version():
    """At least one adapter, not every adapter.

    DirectX writes a feature level only for an adapter it has actually
    initialised. This machine's integrated Radeon has never had a display
    attached, so its subkey carries the identity and the memory sizes but
    no `MaxD3D11FeatureLevel` and no `MaxD3D12FeatureLevel` at all --
    which is a fact about the machine, not a gap in the reader.
    """
    named = [f.directx_version for f in adapter_facts() if f.directx_version]
    assert named, "no adapter reported a DirectX version"
    assert any(version.startswith("12") for version in named)


def test_the_software_adapter_is_recognised_by_its_ids_not_its_name():
    """Microsoft Basic Render Driver is the English name only. The adapter
    is WARP, and WARP has a fixed PCI identity."""
    for facts in adapter_facts():
        if facts.software:
            assert (facts.vendor_id, facts.device_id) == (0x1414, 0x8C)


def test_dedicated_memory_is_not_read_from_wmi():
    """`Win32_VideoController.AdapterRAM` is a signed 32-bit field: this
    machine's 24 GB card reports -1048576 through it. The registry's figure
    is a real 64-bit one."""
    facts = adapter_facts()
    for entry in facts:
        if entry.dedicated_limit is not None:
            assert entry.dedicated_limit >= 0
    biggest = max((entry.dedicated_limit or 0) for entry in facts)
    assert biggest > 2 * 1024 ** 3, "no adapter reported real video memory"


def test_a_fact_that_could_not_be_read_says_so():
    """This machine's integrated adapter has no `MaxD3D12FeatureLevel`
    value at all. That must arrive as None with a reason, not as a
    plausible-looking default."""
    for facts in adapter_facts():
        if facts.directx_version is None:
            assert facts.unavailable, f"{facts.name} refused without saying why"


# ---- the live sampler ---------------------------------------------------

@pytest.fixture
def sampler():
    with GpuSampler() as live:
        yield live


def test_the_first_sample_has_no_utilisation(sampler):
    """The two counter kinds do not arrive together, and the split is not
    arbitrary: memory in use is an instantaneous number and answers at
    once, while utilisation is a percentage over an interval and PDH
    refuses the first read of it with `PDH_INVALID_DATA`. So the first
    tick shows real memory and a dash for load -- never 0%, which would
    claim an idle GPU we have not measured.
    """
    first = sampler.sample()
    assert first, "the first sample reported nothing at all"
    assert all(entry.utilisation is None for entry in first)
    assert any(entry.dedicated_bytes for entry in first)


def test_the_second_sample_reports_the_adapters(sampler):
    sampler.sample()
    time.sleep(0.4)
    usage = sampler.sample()
    assert usage, "the second sample reported no adapters"
    assert all(isinstance(entry, AdapterUsage) for entry in usage)
    assert any(entry.engines for entry in usage), \
        "no adapter reported any engine after two collections"


def test_utilisation_stays_inside_nought_to_a_hundred(sampler):
    sampler.sample()
    time.sleep(0.4)
    for entry in sampler.sample():
        if entry.utilisation is not None:
            assert 0.0 <= entry.utilisation <= 100.0
        for load in entry.engines:
            assert 0.0 <= load.percent <= 100.0


def test_the_adapters_memory_in_use_is_reported(sampler):
    sampler.sample()
    time.sleep(0.4)
    usage = sampler.sample()
    assert any(entry.dedicated_bytes for entry in usage), \
        "no adapter reported any dedicated memory in use"


def test_the_live_sample_joins_to_the_facts_by_luid(sampler):
    """The panel titles each graph with the adapter's name, which only works
    if the live LUID matches a fact row."""
    sampler.sample()
    time.sleep(0.4)
    known = {facts.luid for facts in adapter_facts()}
    matched = [entry for entry in sampler.sample() if entry.luid in known]
    assert matched, "no live adapter matched a known one"


def test_a_sample_is_cheap_enough_for_a_one_second_tick(sampler):
    """The whole point of holding the query open. Measured at 0.33 ms for
    483 engine instances; a budget of 50 ms is loose enough to survive a
    busy build machine and tight enough to catch a rewrite that starts
    re-enumerating the counters on every tick."""
    sampler.sample()
    time.sleep(0.4)
    best = min(_timed(sampler) for _ in range(3))
    assert best < 0.05, f"a GPU sample took {best * 1000:.1f} ms"


def _timed(sampler):
    started = time.perf_counter()
    sampler.sample()
    return time.perf_counter() - started


def test_closing_twice_is_harmless():
    live = GpuSampler()
    live.close()
    live.close()


def test_sampling_after_close_is_none_rather_than_a_crash():
    live = GpuSampler()
    live.sample()
    live.close()
    assert live.sample() is None


# ---- Qt-free ------------------------------------------------------------

def test_the_engine_does_not_import_qt():
    import inspect

    from core.procengine import gpuinfo

    assert "PyQt6" not in inspect.getsource(gpuinfo)
