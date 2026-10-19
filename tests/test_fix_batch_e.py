"""Whole-app review batch E: GPU factory guard, fan marker, PID identity,
display un-staging, energy gaps, log rotation, F5."""
import json
import logging
import os
import types

import pytest

from core import logging_service as ls
from modules.dashboard import energy
from modules.monitor_control import display_writes as dw
from modules.process_explorer import process_actions as pa
from modules.thermal_control.engine import controller as ctl


# ---- thermal -----------------------------------------------------------------------

class _Bridge:
    def __init__(self):
        self.touched = []

    def read(self):
        return []

    def restore_saved_default(self, cid, mode, pwm):
        raise RuntimeError("driver refused")

    def release(self, cid):
        raise RuntimeError("driver refused")

    def release_all(self):
        return []


def test_an_unrecovered_header_survives_ticks_and_shutdown(tmp_path):
    marker = tmp_path / "fan_control_active.json"
    marker.write_text(json.dumps({"pid": 1, "controls": {"ch0": {"mode": 64, "pwm": 249}}}))
    c = ctl.FanController(_Bridge(), str(marker))
    assert c.recover() == []
    c._write_marker([])                          # what a tick with nothing touched writes
    assert "ch0" in json.loads(marker.read_text())["controls"]
    c.shutdown()
    saved = json.loads(marker.read_text())["controls"]
    assert saved["ch0"] == {"mode": 64, "pwm": 249}


def test_the_marker_is_replaced_atomically(tmp_path):
    marker = tmp_path / "m.json"
    c = ctl.FanController(_Bridge(), str(marker))
    c._write_marker(["a"])
    assert json.loads(marker.read_text())["controls"] == {"a": None}
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_back_to_factory_without_a_baseline_resets_nothing(monkeypatch):
    from modules.thermal_control import thermal_service as ts
    from modules.thermal_control.engine import adlx
    called = []
    monkeypatch.setattr(adlx, "reset_to_factory", lambda *a: called.append(a))
    svc = ts.ThermalService.__new__(ts.ThermalService)
    store = {}
    svc._config_get = lambda k, d=None: store.get(k, d)
    svc._config_set = lambda k, v: store.__setitem__(k, v)
    svc._gpu_job = lambda fn, cb: called.append("job")
    msg = svc.gpu_back_to_factory("AMD Radeon RX 7900 XTX")
    assert msg and "never changed" in msg and called == []


# ---- processes ---------------------------------------------------------------------

def test_a_reused_pid_is_not_killed(monkeypatch):
    killed = []

    class _P:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return 500.0

        def name(self):
            return "victim.exe"

        def kill(self):
            killed.append(self.pid)

    monkeypatch.setattr(pa.psutil, "Process", _P)
    ok, err = pa.kill_process(10, created=100.0)
    assert not ok and "not ended" in err and killed == []
    ok, errors = pa.kill_tree(10, created=100.0)
    assert not ok and killed == []
    ok, _err = pa.kill_process(10, created=500.0)
    assert ok and killed == [10]


# ---- display -----------------------------------------------------------------------

def test_a_failed_batch_unstages_what_it_staged(monkeypatch):
    staged = []
    mode = types.SimpleNamespace(width=2560, height=1440, refresh_hz=144.0)
    monkeypatch.setattr(dw, "mode_is_offered", lambda *a: (True, ""))
    monkeypatch.setattr(dw.dm, "modes_for", lambda d: [])
    monkeypatch.setattr(dw.dm, "current_mode", lambda d: mode)

    def stage(device, w, h, r):
        staged.append((device, w, h, r))
        return -1 if device == "B" else 0
    monkeypatch.setattr(dw, "stage_mode", stage)
    monkeypatch.setattr(dw, "change_succeeded", lambda rc: rc == 0)
    monkeypatch.setattr(dw, "change_result_name", lambda rc: f"rc {rc}")
    monkeypatch.setattr(dw, "commit_staged_modes", lambda: pytest.fail("must not commit"))
    ok, reason = dw.apply_modes([("A", 1920, 1080, 60.0), ("B", 1920, 1080, 60.0)])
    assert not ok and "B" in reason
    assert staged[-1] == ("A", 2560, 1440, 144.0)


# ---- energy ------------------------------------------------------------------------

def test_a_missing_package_counter_is_unknown_not_zero():
    m = energy.EnergyMeter.__new__(energy.EnergyMeter)
    m._inst = {"pkg": ["_Total", "pkg1"], "core": []}
    m._query = types.SimpleNamespace(read=lambda: {"_Total": 30000.0})
    m.joules, m._last_t = 0.0, None
    reading = m.read()
    assert reading.package_w is None and m.joules == 0.0


# ---- logging -----------------------------------------------------------------------

def test_app_log_rotates_by_default():
    assert ls.LoggingService()._max_bytes == ls.LoggingService.MAX_BYTES


def test_a_failed_rollover_keeps_logging(tmp_path, monkeypatch):
    h = ls._SafeRotatingFileHandler(str(tmp_path / "app.log"), maxBytes=10, backupCount=2,
                                    encoding="utf-8")

    def refuse(*a):
        raise PermissionError(32, "being used by another process")
    monkeypatch.setattr(h, "rotate", refuse)
    h.setFormatter(logging.Formatter("%(message)s"))
    for i in range(3):
        h.emit(logging.LogRecord("x", logging.INFO, "", 0, "line %d" % i, None, None))
    h.close()
    assert h.maxBytes == 0
    assert "line 2" in (tmp_path / "app.log").read_text(encoding="utf-8")
