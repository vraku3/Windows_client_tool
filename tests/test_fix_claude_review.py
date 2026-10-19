"""Findings from the Claude review of the whole-app fix batches (2026-10-19)."""
import types

from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.profile import import_profile, plan_baseline
from modules.monitor_control import display_writes as dw
from modules.app_buster.engine import actions as act


def test_a_profile_value_that_cannot_be_written_is_skipped_not_fatal():
    catalog = load_catalog()
    readings = {"password_min_length": 14, "ntlm_level": 5}
    staged = import_profile({"controls": {"password_min_length": 0, "ntlm_level": 3}},
                            catalog, readings=readings)
    assert {cid for cid, _why in staged.skipped} == {"password_min_length", "ntlm_level"}


def test_the_hardened_baseline_plans_without_raising():
    catalog = load_catalog()
    readings = {cid: None for cid in catalog}
    plan = plan_baseline("hardened", catalog, readings=readings)
    reasons = {s["id"]: s["reason"] for s in plan["skipped"]}
    assert "defender_cloud_block_level" in reasons
    assert reasons["defender_cloud_block_level"].startswith("not staged")


def test_a_failed_commit_unstages_too(monkeypatch):
    staged = []
    mode = types.SimpleNamespace(width=2560, height=1440, refresh_hz=144.0)
    monkeypatch.setattr(dw, "mode_is_offered", lambda *a: (True, ""))
    monkeypatch.setattr(dw.dm, "modes_for", lambda d: [])
    monkeypatch.setattr(dw.dm, "current_mode", lambda d: mode)
    monkeypatch.setattr(dw, "stage_mode", lambda d, w, h, r: staged.append((d, w, h, r)) or 0)
    monkeypatch.setattr(dw, "change_succeeded", lambda rc: rc == 0)
    monkeypatch.setattr(dw, "change_result_name", lambda rc: f"rc {rc}")
    monkeypatch.setattr(dw, "commit_staged_modes", lambda: -1)
    ok, reason = dw.apply_modes([("A", 1920, 1080, 60.0), ("B", 1920, 1080, 60.0)])
    assert not ok and "commit failed" in reason
    assert staged[-2:] == [("A", 2560, 1440, 144.0), ("B", 2560, 1440, 144.0)]


class _Runner(act.Runner):
    def __init__(self):
        self.calls = 0

    def installed_families(self):
        self.calls += 1
        return {"other_x"} if self.calls > 1 else {"acme.app_123"}

    def all_user_families(self):
        return {"other_x"}

    def provisioned_names(self):
        return None                      # unreadable

    def powershell(self, script, timeout=180):
        return 0, "", ""


def test_entire_pc_still_deletes_the_data_when_provisioning_is_unconfirmed(monkeypatch):
    deleted = []
    monkeypatch.setattr(act, "profile_data_folders", lambda fam: [r"C:\Users\u\AppData\Local\Packages\Acme.App_123"])
    monkeypatch.setattr(act, "delete_folder", lambda f: deleted.append(f) or (True, []))
    rec = types.SimpleNamespace(removable=True, full_name="Acme.App_1.0_x64__123", family="Acme.App_123",
                                package_name="Acme.App", install_location="")
    o = act.remove_windows_app(rec, act.SCOPE_PC, _Runner(), lambda line: None)
    assert deleted and o.state == act.SKIPPED and "unconfirmed" in o.reason


def test_back_to_factory_uses_its_baseline_once(monkeypatch):
    from modules.thermal_control import thermal_service as ts
    svc = ts.ThermalService.__new__(ts.ThermalService)
    store = {ts.GPU_FACTORY_KEY: {"GPU": {"factory": True, "curve": []}}}
    svc._config_get = lambda k, d=None: store.get(k, d)
    svc._config_set = lambda k, v: store.__setitem__(k, v)
    svc._gpu_job = lambda fn, cb: None
    assert svc.gpu_back_to_factory("GPU") is None
    assert "GPU" not in store[ts.GPU_FACTORY_KEY]
    assert "never changed" in svc.gpu_back_to_factory("GPU")
