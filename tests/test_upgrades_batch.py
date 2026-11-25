"""Unit tests for the v2 upgrade batch: WmiCache, MemoryStandby, NVML, and QuickFix action."""
import time
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from core.memory_standby import get_memory_snapshot
from core.wmi_cache import WmiCache
from modules.quick_fix.fix_actions import ALL_ACTIONS
from modules.thermal_control.engine import nvml
from ui.table_resizer import _table_persist_key


def test_wmi_cache_ttl_and_invalidate():
    cache = WmiCache(default_ttl=5.0)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [1, 2, calls["n"]]

    r1 = cache.get_or_query("pnp.devices", fetch)
    r2 = cache.get_or_query("pnp.devices", fetch)
    assert r1 == r2 == [1, 2, 1]
    assert calls["n"] == 1

    cache.invalidate("pnp.")
    r3 = cache.get_or_query("pnp.devices", fetch)
    assert r3 == [1, 2, 2]
    assert calls["n"] == 2

    # Short TTL expiry
    r4 = cache.get_or_query("short.key", fetch, ttl=0.01)
    time.sleep(0.03)
    r5 = cache.get_or_query("short.key", fetch, ttl=0.01)
    assert r4 != r5


def test_memory_standby_snapshot_reads_real_counters():
    snap = get_memory_snapshot()
    assert snap.total_mb > 0
    assert snap.available_mb >= 0
    assert 0 <= snap.load_pct <= 100
    assert snap.commit_limit_mb >= snap.total_mb * 0.5


def test_nvml_gracefully_handles_missing_or_non_nvidia():
    gpus = nvml.read_gpus()
    sensors = nvml.read_sensors()
    assert isinstance(gpus, list)
    assert isinstance(sensors, list)


def test_quick_fix_includes_purge_standby_ram():
    keys = {a.key for a in ALL_ACTIONS}
    assert "purge_standby_ram" in keys


def test_table_persist_key_is_stable(qapp):
    t1 = QTableWidget(1, 2)
    t1.setObjectName("servicesTable")
    t1.setHorizontalHeaderLabels(["Name", "Status"])
    t1.setItem(0, 0, QTableWidgetItem("wuauserv"))

    t2 = QTableWidget(1, 2)
    t2.setObjectName("servicesTable")
    t2.setHorizontalHeaderLabels(["Name", "Status"])

    assert _table_persist_key(t1) is not None
    assert _table_persist_key(t1) == _table_persist_key(t2)
