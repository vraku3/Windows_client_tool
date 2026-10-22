"""Optimise OS uses an exact-state journal; no test writes to Windows."""
import copy
import json

import pytest

from modules.game_mode.engine import optimise as opt


SCHEME = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeSystem:
    def __init__(self):
        self.admin = True
        self.active = SCHEME
        self.states = {spec.id: {"exists": False, "key_exists": True}
                       for spec in opt.ITEMS if spec.kind == "registry"}
        self.states.update({
            "menu_delay": {"exists": True, "key_exists": True, "value": "200", "type": 1,
                           "live": 250},
            "hover_delay": {"exists": True, "key_exists": True, "value": "400", "type": 1,
                            "live": 400},
            "sysmain": {"exists": True, "start_type": 4, "delayed": True, "running": False},
            "usb_suspend": {"scheme": SCHEME, "ac": 1},
            "power_plan": {"scheme": SCHEME, "name": "Test plan"},
            # Measured on the owner's PC (.optimise-facts, 2026-10-21).
            "remote_assistance": {"exists": True, "key_exists": True, "value": 1, "type": 4},
            "retail_demo": {"exists": True, "start_type": 3, "delayed": False, "running": False},
            "snmp_trap": {"exists": True, "start_type": 3, "delayed": False, "running": False},
            "chrome_background": {"exists": False, "key_exists": True},
            "brave_background": {"exists": False, "key_exists": False},
            "ink_workspace": {"exists": False, "key_exists": False},
            "work_folders": {"exists": True, "enabled": True},
            "smb_direct": {"exists": True, "enabled": True},
        })
        self.states.update({spec.id: {"exists": True, "installed": True}
                            for spec in opt.ITEMS if spec.kind == "capability"})
        self.refused = set()
        self.fail_write = set()
        self.writes = []
        self.before_write = None
        self.power = {SCHEME: 1, OTHER: 1}
        self.preconditions = {"chrome": True, "brave": True, "no_pen_touch": True, "no_rdma": True}
        self.services = {"wisvc": {"exists": True, "start_type": 4, "delayed": False, "running": False},
                         "XboxGipSvc": {"exists": True, "start_type": 4, "delayed": False, "running": False}}
        self.is_insider = True
        self.restart = set()

    def is_admin(self):
        return self.admin

    def precondition(self, name):
        answer = self.preconditions[name]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def service_state(self, name):
        return copy.deepcopy(self.services[name])

    def insider(self):
        return self.is_insider

    def read(self, spec, scheme=None):
        if spec.id in self.refused:
            raise PermissionError("access denied")
        if spec.id == "usb_suspend":
            guid = scheme or self.active
            return {"scheme": guid, "ac": self.power[guid]}
        return copy.deepcopy(self.states[spec.id])

    def write(self, spec, state):
        if self.before_write:
            self.before_write(spec, state)
        if spec.id in self.fail_write:
            raise PermissionError("write refused")
        self.writes.append((spec.id, copy.deepcopy(state)))
        if spec.id == "usb_suspend":
            self.power[state["scheme"]] = state["ac"]
        else:
            self.states[spec.id] = copy.deepcopy(state)
        if spec.id in self.restart:
            return {"restart": True}
        return None


@pytest.fixture
def engine(tmp_path):
    return opt.Optimiser(FakeSystem(), str(tmp_path))


def test_profiles_exclude_optional_and_read_only_items(engine):
    assert opt.profile_ids("tailored") == {"menu_delay", "hover_delay", "explorer_home", "folder_tips",
                                           "remote_assistance", "retail_demo"}
    assert opt.profile_ids("maximum") == {"menu_delay", "hover_delay", "explorer_home", "folder_tips",
                                          "remote_assistance", "retail_demo", "sysmain", "usb_suspend",
                                          "chrome_background", "brave_background",
                                          "cap_handwriting", "cap_math", "cap_steps"}
    optional = {"background_apps", "ink_workspace", "work_folders", "smb_direct", "ie_mode", "legacy_wmp",
                "vbscript", "snmp_trap", "cap_print_console"}
    assert not optional & opt.profile_ids("maximum")
    assert all(opt.BY_ID[i].level == "O" for i in optional)
    assert all(not spec.read_only for spec in opt.ITEMS if spec.id in opt.profile_ids("maximum"))


@pytest.mark.parametrize("item_id,target", [
    ("menu_delay", "0"), ("hover_delay", "100"), ("explorer_home", 1),
    ("folder_tips", 0), ("background_apps", 1),
])
def test_registry_read_target_and_status(engine, item_id, target):
    row = next(r for r in engine.inspect({item_id}) if r.id == item_id)
    assert row.status == "Will change"
    assert row.target["value"] == target
    assert row.before == engine.system.states[item_id]
    result = engine.apply({item_id})
    assert result.changed == [item_id]
    assert next(r for r in engine.inspect({item_id}) if r.id == item_id).status == "Changed by Optimise OS"


@pytest.mark.parametrize("item_id,target", [
    ("sysmain", {"exists": True, "start_type": 2, "delayed": False, "running": True}),
    ("usb_suspend", {"scheme": SCHEME, "ac": 0}),
])
def test_service_and_power_targets(engine, item_id, target):
    row = next(r for r in engine.inspect({item_id}) if r.id == item_id)
    assert row.status == "Will change"
    assert row.target == target
    engine.apply({item_id})
    assert next(r for r in engine.inspect({item_id}) if r.id == item_id).status == "Changed by Optimise OS"


def test_journal_is_on_disk_before_every_write_and_keeps_original(engine):
    originals = copy.deepcopy(engine.system.states)

    def check(spec, state):
        data = json.loads(engine.path.read_text(encoding="utf-8"))
        entry = next(e for e in data["items"] if e["id"] == spec.id)
        assert entry["done"] is False
        assert entry["before"] == originals[spec.id]

    engine.system.before_write = check
    engine.apply(opt.profile_ids("tailored"))
    first = engine.path.read_bytes()
    engine.apply(opt.profile_ids("tailored"))
    assert engine.path.read_bytes() == first
    engine.system.states["menu_delay"]["value"] = "300"
    engine.apply({"menu_delay"})
    assert json.loads(engine.path.read_text())["items"][0]["before"] == originals["menu_delay"]


def test_refused_read_leaves_item_alone(engine):
    engine.system.refused.add("menu_delay")
    result = engine.apply({"menu_delay"})
    assert not engine.system.writes
    assert "Could not read" in result.skipped["menu_delay"]
    assert not engine.path.exists()


def test_roundtrip_exact_type_absence_service_and_power(engine):
    engine.system.states["explorer_home"] = {"exists": True, "key_exists": True, "type": 1, "value": "2"}
    engine.system.states["folder_tips"]["key_exists"] = False
    before = copy.deepcopy(engine.system.states)
    engine.apply(opt.profile_ids("maximum") | {"background_apps"})
    engine.system.active = OTHER
    result = engine.revert()
    assert not result.failed
    assert engine.system.states == before
    assert engine.system.power == {SCHEME: 1, OTHER: 1}
    assert engine.system.active == OTHER
    assert not engine.path.exists()
    assert [item for item, _ in engine.system.writes[-7:]] == [
        "background_apps", "usb_suspend", "sysmain", "folder_tips", "explorer_home", "hover_delay", "menu_delay"]


def test_partial_revert_keeps_only_unresolved_items(engine):
    engine.apply(opt.profile_ids("tailored"))
    engine.system.fail_write.add("hover_delay")
    result = engine.revert()
    assert "hover_delay" in result.failed
    assert [e["id"] for e in json.loads(engine.path.read_text())["items"]] == ["hover_delay"]
    engine.system.fail_write.clear()
    engine.revert()
    assert not engine.path.exists()


@pytest.mark.parametrize("payload", [b"{bad", b'{"version":1,"items":[]}',
                                     b'{"version":1,"items":[{"id":"menu_delay","before":null,"done":true}]}'])
def test_corrupt_journal_is_untouched(engine, payload):
    engine.path.write_bytes(payload)
    for action in (lambda: engine.apply({"menu_delay"}), engine.revert):
        with pytest.raises(opt.OptimiseError, match="journal"):
            action()
        assert engine.path.read_bytes() == payload
    assert not engine.system.writes


def _game_mode_on(engine, *actions):
    session = {"version": 1, "started": 1.0, "actions": list(actions)}
    (engine.path.parent / "session.json").write_text(json.dumps(session), encoding="utf-8")


GM_SYSMAIN = {"kind": "service_stop", "name": "SysMain", "label": "SysMain", "done": True}
GM_PLAN = {"kind": "plan", "before": SCHEME, "before_name": "Balanced", "after": OTHER, "done": True}


def test_game_mode_on_holds_only_sysmain_and_usb_and_applies_the_rest(engine):
    _game_mode_on(engine, GM_SYSMAIN, GM_PLAN)
    result = engine.apply(opt.profile_ids("maximum"))
    assert result.skipped["sysmain"] == "Game Mode is holding SysMain stopped -- applied after Game Mode OFF"
    assert result.skipped["usb_suspend"] == "Game Mode switched the power plan -- applied after Game Mode OFF"
    written = {item for item, _state in engine.system.writes}
    assert "sysmain" not in written and "usb_suspend" not in written
    assert {"menu_delay", "hover_delay", "remote_assistance", "retail_demo"} <= set(result.changed)
    assert engine.system.power == {SCHEME: 1, OTHER: 1}


def test_game_mode_on_without_sysmain_or_plan_actions_holds_nothing(engine):
    _game_mode_on(engine, {"kind": "service_stop", "name": "WSearch", "done": True})
    result = engine.apply({"sysmain", "usb_suspend"})
    assert result.changed == ["sysmain", "usb_suspend"]


def test_corrupt_game_mode_journal_holds_only_the_two_overlapping_items(engine):
    (engine.path.parent / "session.json").write_text("broken", encoding="utf-8")
    result = engine.apply({"menu_delay", "sysmain", "usb_suspend"})
    assert result.changed == ["menu_delay"]
    assert set(result.skipped) == {"sysmain", "usb_suspend"}
    assert (engine.path.parent / "session.json").read_text(encoding="utf-8") == "broken"


def test_revert_defers_game_mode_holds_and_restores_the_rest(engine):
    engine.apply(opt.profile_ids("maximum"))
    _game_mode_on(engine, GM_SYSMAIN, GM_PLAN)
    result = engine.revert()
    assert not result.failed
    assert set(result.skipped) == {"sysmain", "usb_suspend"}
    assert engine.system.states["sysmain"]["start_type"] == 2
    assert engine.system.power[SCHEME] == 0
    assert engine.system.states["menu_delay"]["value"] == "200"
    assert {e["id"] for e in engine._load()["items"]} == {"sysmain", "usb_suspend"}
    (engine.path.parent / "session.json").unlink()
    assert not engine.revert().failed
    assert engine.system.states["sysmain"]["start_type"] == 4
    assert engine.system.power[SCHEME] == 1
    assert not engine.path.exists()


def test_preview_says_which_items_wait_for_game_mode_off(engine):
    _game_mode_on(engine, GM_SYSMAIN, GM_PLAN)
    rows = {r.id: r for r in engine.inspect(opt.profile_ids("maximum"))}
    assert rows["sysmain"].status == rows["usb_suspend"].status == "After Game Mode OFF"
    assert "applied after Game Mode OFF" in rows["sysmain"].why
    assert rows["menu_delay"].status == "Will change"


def test_unelevated_hkcu_still_works_and_admin_items_are_skipped(engine):
    engine.system.admin = False
    result = engine.apply(opt.profile_ids("maximum"))
    assert result.changed == ["menu_delay", "hover_delay", "explorer_home", "folder_tips"]
    assert result.skipped["sysmain"] == result.skipped["usb_suspend"] == "Needs administrator"


def test_read_only_rows_report_measurements_and_never_write(engine):
    engine.system.states["window_animations"] = {"exists": True, "key_exists": True, "value": "0", "type": 1}
    engine.system.states["transparency"] = {"exists": True, "key_exists": True, "value": 1, "type": 4}
    rows = {r.id: r for r in engine.inspect(set())}
    assert rows["window_animations"].status == "Already optimal"
    assert rows["transparency"].status == "Not applicable"
    assert "Tweaks" in rows["transparency"].why
    assert rows["power_plan"].before["name"] == "Test plan"
    engine.apply({"window_animations", "transparency", "power_plan"})
    assert not engine.system.writes


def test_missing_service_is_not_applicable(engine):
    engine.system.states["sysmain"] = {"exists": False}
    result = engine.apply({"sysmain"})
    assert result.skipped["sysmain"] == "Not applicable"
    assert not engine.system.writes


def test_failed_write_or_readback_keeps_pending_before_state(engine):
    engine.system.fail_write.add("menu_delay")
    result = engine.apply({"menu_delay"})
    assert "menu_delay" in result.failed
    entry = json.loads(engine.path.read_text())["items"][0]
    assert entry["before"]["value"] == "200"
    assert entry["done"] is False
    engine.system.fail_write.clear()
    engine.revert()
    assert not engine.path.exists()


def test_excluded_folklore_is_never_an_item():
    excluded = {"NetworkThrottlingIndex", "SystemResponsiveness", "Win32PrioritySeparation",
                "Prefetch", "Search", "pagefile", "LargeSystemCache", "timer resolution",
                "useplatformclock", "disabledynamictick", "core parking", "mitigations",
                "Defender", "HVCI", "VBS", "firewall", "GPU", "HAGS", "TDR"}
    import re
    for spec in opt.ITEMS:
        # Whole words: "VBScript" (a capability) is not "VBS" (virtualization-based security).
        text = re.sub(r"[_\W]+", " ", spec.id + " " + spec.label).casefold()
        assert not any(re.search(rf"\b{re.escape(name.casefold())}\b", text) for name in excluded)


def test_changed_since_confirmation_is_skipped(engine):
    expected = {r.id: r.before for r in engine.inspect({"menu_delay"}) if r.id == "menu_delay"}
    engine.system.states["menu_delay"]["value"] = "350"
    result = engine.apply({"menu_delay"}, expected=expected)
    assert not result.changed
    assert "confirmation" in result.skipped["menu_delay"]
    assert not engine.path.exists()


def test_new_active_scheme_cannot_replace_original_power_before_state(engine):
    engine.apply({"usb_suspend"})
    engine.system.active = OTHER
    result = engine.apply({"usb_suspend"})
    assert "Revert" in result.skipped["usb_suspend"]
    assert engine.system.power[OTHER] == 1


def test_tab_build_is_cheap_and_refresh_uses_worker(engine, qapp):
    from modules.game_mode.optimise_tab import OptimiseTab

    class Pool:
        def __init__(self):
            self.workers = []

        def start(self, worker):
            self.workers.append(worker)

    pool = Pool()
    tab = OptimiseTab(pool, optimiser=engine)
    assert not pool.workers
    tab.refresh()
    assert len(pool.workers) == 1
    pool.workers[0].run()
    assert tab._tree.topLevelItemCount() == len(opt.ITEMS)
    assert not tab.busy
    assert tab._selected == opt.profile_ids("tailored")
    tab.close()


def test_readback_failure_never_marks_done(engine, monkeypatch):
    monkeypatch.setattr(engine.system, "write", lambda spec, state: None)
    result = engine.apply({"menu_delay"})
    assert "read-back" in result.failed["menu_delay"]
    assert json.loads(engine.path.read_text())["items"][0]["done"] is False


def test_refused_restore_read_keeps_original_journal(engine):
    engine.apply({"menu_delay"})
    before = engine.path.read_bytes()
    engine.system.refused.add("menu_delay")
    result = engine.revert()
    assert "menu_delay" in result.failed
    assert engine.path.read_bytes() == before


def test_journal_write_failure_prevents_machine_change(engine, monkeypatch):
    def refused(data):
        raise PermissionError("cannot journal")

    monkeypatch.setattr(engine, "_save", refused)
    with pytest.raises(PermissionError, match="journal"):
        engine.apply({"menu_delay"})
    assert not engine.system.writes


@pytest.mark.parametrize("start,delayed,running", [
    (2, True, False), (2, False, True), (3, False, False), (4, True, False), (4, False, True),
])
def test_native_service_restore_preserves_config_and_running_state(monkeypatch, start, delayed, running):
    import win32service as svc

    state = {"start": 2, "delayed": False, "running": True}
    monkeypatch.setattr(svc, "OpenSCManager", lambda *args: 10)
    monkeypatch.setattr(svc, "OpenService", lambda *args: 20)
    monkeypatch.setattr(svc, "CloseServiceHandle", lambda handle: None)
    monkeypatch.setattr(svc, "QueryServiceConfig", lambda handle: (0, state["start"]))
    monkeypatch.setattr(svc, "QueryServiceConfig2", lambda *args: state["delayed"])
    monkeypatch.setattr(svc, "QueryServiceStatus", lambda handle: (0, svc.SERVICE_RUNNING if state["running"] else svc.SERVICE_STOPPED))

    def config(handle, service_type, start_type, *args):
        state["start"] = start_type

    def config2(handle, kind, value):
        if state["start"] != 2:
            raise OSError("Delayed auto-start requires Automatic while setting the flag")
        state["delayed"] = value

    def start_service(*args):
        if state["start"] == 4:
            raise OSError("disabled")
        state["running"] = True

    monkeypatch.setattr(svc, "ChangeServiceConfig", config)
    monkeypatch.setattr(svc, "ChangeServiceConfig2", config2)
    monkeypatch.setattr(svc, "StartService", start_service)
    monkeypatch.setattr(svc, "ControlService", lambda *args: state.update(running=False))
    spec = opt.BY_ID["sysmain"]
    before = {"exists": True, "start_type": start, "delayed": delayed, "running": running}
    system = opt.WindowsSystem()
    spec.revert(system, before)
    assert spec.read(system) == before


@pytest.mark.parametrize("still_active", [True, False])
def test_native_power_restore_pins_guid_and_only_refreshes_if_active(monkeypatch, still_active):
    from types import SimpleNamespace

    values = {SCHEME: 0, OTHER: 1}
    activations = []

    def write(root, scheme, subgroup, setting, index):
        assert subgroup._obj.text() == "2a737441-1930-4402-8d77-b2bebba308a3"
        assert setting._obj.text() == "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
        values[scheme._obj.text()] = index
        return 0

    def activate(root, scheme):
        activations.append(scheme._obj.text())
        return 0

    power = SimpleNamespace(PowerWriteACValueIndex=write, PowerSetActiveScheme=activate)
    monkeypatch.setattr(opt.WindowsSystem, "_power", staticmethod(lambda: power))
    monkeypatch.setattr(opt.WindowsSystem, "_active_scheme", lambda self: SCHEME if still_active else OTHER)
    opt.WindowsSystem().write(opt.BY_ID["usb_suspend"], {"scheme": SCHEME, "ac": 1})
    assert values == {SCHEME: 1, OTHER: 1}
    assert activations == ([SCHEME] if still_active else [])


@pytest.mark.parametrize("absent", [True, False])
def test_native_live_restore_puts_raw_type_or_absence_back_after_spi(monkeypatch, absent):
    import winreg

    raw = {"value": ("0", 1), "live": 0}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def query(key, name):
        if raw["value"] is None:
            raise FileNotFoundError("absent")
        return raw["value"]

    def spi(action, param, pointer, flags):
        if flags:
            assert action == 0x006B
            assert flags == 3
            raw.update(live=param, value=(str(param), 1))
        else:
            assert action == 0x006A
            pointer._obj.value = raw["live"]

    monkeypatch.setattr(winreg, "OpenKey", lambda *args: Key())
    monkeypatch.setattr(winreg, "CreateKeyEx", lambda *args: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", query)
    monkeypatch.setattr(winreg, "SetValueEx", lambda key, name, reserved, kind, value: raw.update(value=(value, kind)))
    monkeypatch.setattr(winreg, "DeleteValue", lambda *args: raw.update(value=None))
    monkeypatch.setattr(opt.WindowsSystem, "_spi", staticmethod(spi))
    before = {"exists": False, "key_exists": True, "live": 250} if absent else {
        "exists": True, "key_exists": True, "type": 2, "value": "200", "live": 250}
    spec = opt.BY_ID["menu_delay"]
    system = opt.WindowsSystem()
    spec.revert(system, before)
    assert spec.read(system) == before


def test_apply_confirmation_defaults_cancel_and_lists_only_changes(engine, qapp, monkeypatch):
    from PyQt6.QtWidgets import QDialog
    from modules.game_mode import confirm_dialog
    from modules.game_mode.optimise_tab import OptimiseTab

    queue = []

    class Pool:
        def start(self, worker):
            queue.append(worker)

    dialogs = []

    def cancel(dialog):
        dialogs.append((dialog.lead.text(), dialog.details.toPlainText()))
        assert dialog.cancel_button.isDefault()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(confirm_dialog.ConfirmDialog, "exec", cancel)
    tab = OptimiseTab(Pool(), optimiser=engine)
    tab._preview_apply()
    queue.pop(0).run()
    assert len(dialogs) == 1
    assert dialogs[0][0] == "Apply exactly these changes?"
    assert "Menu open delay" in dialogs[0][1]
    assert "Explorer opens to This PC" in dialogs[0][1]
    assert "Re-enable SysMain" not in dialogs[0][1]
    assert not engine.system.writes
    assert not engine.path.exists()
    assert not tab.busy
    tab.close()


def test_worker_callbacks_ignore_deleted_tab(engine, qapp):
    from PyQt6 import sip
    from modules.game_mode.optimise_tab import OptimiseTab

    queue = []

    class Pool:
        def start(self, worker):
            queue.append(worker)

    tab = OptimiseTab(Pool(), optimiser=engine)
    tab.refresh()
    sip.delete(tab)
    queue.pop(0).run()
    assert not engine.system.writes


@pytest.mark.parametrize("before", [None, {"exists": True, "key_exists": True, "value": "oops", "type": 1}])
def test_unparseable_before_state_is_never_journaled(engine, before):
    engine.system.states["explorer_home"] = before
    result = engine.apply({"explorer_home"})
    assert "Could not read" in result.skipped["explorer_home"]
    assert not engine.system.writes
    assert not engine.path.exists()


@pytest.mark.parametrize("item_id,value,kind", [
    ("window_animations", "0", 1), ("taskbar_animations", 0, 4), ("transparency", 0, 4),
    ("aero_peek", 0, 4), ("keyboard_delay", "0", 1), ("keyboard_speed", "31", 1),
    ("startup_delay", 0, 4), ("game_dvr", 0, 4), ("app_capture", 0, 4), ("hibernation", 0, 4),
])
def test_each_read_only_registry_row_reports_its_measured_optimal_state(engine, item_id, value, kind):
    measured = {"exists": True, "key_exists": True, "value": value, "type": kind}
    engine.system.states[item_id] = measured
    row = next(r for r in engine.inspect(set()) if r.id == item_id)
    assert row.status == "Already optimal"
    assert row.before == measured
    engine.apply({item_id})
    assert not engine.system.writes


def test_unelevated_revert_keeps_admin_entries_but_restores_hkcu(engine):
    engine.apply(opt.profile_ids("maximum"))
    engine.system.admin = False
    result = engine.revert()
    admin_ids = [s.id for s in opt.ITEMS if s.id in opt.profile_ids("maximum") and s.admin]
    assert {"sysmain", "usb_suspend", "remote_assistance", "retail_demo"} <= set(admin_ids)
    assert set(result.failed) == set(admin_ids)
    assert [e["id"] for e in json.loads(engine.path.read_text())["items"]] == admin_ids
    assert engine.system.states["menu_delay"]["value"] == "200"


def test_cancelled_apply_has_no_side_effects(engine):
    result = engine.apply(opt.profile_ids("maximum"), cancelled=lambda: True)
    assert not result.changed
    assert not engine.system.writes
    assert not engine.path.exists()


def test_cancel_during_confirmation_does_not_schedule_apply(engine, qapp, monkeypatch):
    from modules.game_mode.optimise_tab import OptimiseTab

    queue = []

    class Pool:
        def start(self, worker):
            queue.append(worker)

    tab = OptimiseTab(Pool(), optimiser=engine)

    def confirm(*args):
        tab.cancel()
        return True

    monkeypatch.setattr(tab, "_confirm", confirm)
    tab._preview_apply()
    queue.pop(0).run()
    assert not queue
    assert not engine.system.writes
    assert not tab.busy
    tab.close()


# ---- Extension: T5/T6, M3/M4, O2-O8, capabilities, Free up space, app usage, notes ----------

NEW_REVERSIBLE = ["remote_assistance", "retail_demo", "chrome_background", "brave_background",
                  "ink_workspace", "work_folders", "smb_direct", "snmp_trap", "ie_mode", "legacy_wmp",
                  "vbscript", "cap_handwriting", "cap_math", "cap_steps", "cap_print_console"]


@pytest.mark.parametrize("item_id", NEW_REVERSIBLE)
def test_new_item_read_apply_and_exact_revert(engine, item_id):
    before = copy.deepcopy(engine.system.states[item_id])
    row = next(r for r in engine.inspect({item_id}) if r.id == item_id)
    assert row.status == "Will change" and row.offered
    assert row.before == before
    result = engine.apply({item_id})
    assert result.changed == [item_id]
    assert engine.system.states[item_id] == opt.BY_ID[item_id].target(before)
    entry = json.loads(engine.path.read_text())["items"][0]
    assert entry == {"id": item_id, "before": before, "done": True}
    result = engine.revert()
    assert result.changed == [item_id] and not result.failed
    assert engine.system.states[item_id] == before
    assert not engine.path.exists()


def test_new_targets_are_exactly_the_brief(engine):
    states = engine.system.states
    assert opt.BY_ID["remote_assistance"].target(states["remote_assistance"]) == {
        "exists": True, "key_exists": True, "value": 0, "type": 4}
    spec = opt.BY_ID["remote_assistance"]
    assert (spec.hive, spec.key, spec.name) == (
        "HKLM", r"SYSTEM\CurrentControlSet\Control\Remote Assistance", "fAllowToGetHelp")
    assert opt.BY_ID["retail_demo"].target(states["retail_demo"]) == {
        "exists": True, "start_type": 4, "delayed": False, "running": False}
    for item_id, key in (("chrome_background", r"SOFTWARE\Policies\Google\Chrome"),
                         ("brave_background", r"SOFTWARE\Policies\BraveSoftware\Brave")):
        spec = opt.BY_ID[item_id]
        assert (spec.hive, spec.key, spec.name, spec.value, spec.level) == (
            "HKLM", key, "BackgroundModeEnabled", 0, "M")
        assert "PWAs and extensions stop running" in spec.why
    assert opt.BY_ID["ink_workspace"].key == r"SOFTWARE\Policies\Microsoft\WindowsInkWorkspace"
    assert all(opt.BY_ID[i].admin for i in NEW_REVERSIBLE)


def test_service_revert_restores_recorded_start_type_and_delayed_flag(engine):
    engine.system.states["retail_demo"] = {"exists": True, "start_type": 2, "delayed": True, "running": True}
    before = copy.deepcopy(engine.system.states["retail_demo"])
    engine.apply({"retail_demo"})
    assert engine.system.states["retail_demo"]["start_type"] == 4
    engine.revert()
    assert engine.system.states["retail_demo"] == before


def test_policy_key_the_profile_created_is_recorded_and_removed(engine):
    engine.apply({"brave_background"})
    entry = json.loads(engine.path.read_text())["items"][0]
    assert entry["before"] == {"exists": False, "key_exists": False}
    engine.revert()
    assert engine.system.writes[-1] == ("brave_background", {"exists": False, "key_exists": False})


@pytest.mark.parametrize("answer,text", [(False, "not installed"), (None, "Could not tell"),
                                         (PermissionError("denied"), "Could not tell")])
def test_browser_policy_only_when_browser_installed(engine, answer, text):
    engine.system.preconditions["chrome"] = answer
    row = next(r for r in engine.inspect({"chrome_background"}) if r.id == "chrome_background")
    assert row.status == "Not applicable" and not row.offered
    assert text in row.why
    result = engine.apply({"chrome_background"})
    assert "chrome_background" in result.skipped
    assert not engine.system.writes and not engine.path.exists()


@pytest.mark.parametrize("item_id", ["ink_workspace", "cap_handwriting", "cap_math"])
@pytest.mark.parametrize("answer", [False, None])
def test_pen_items_offered_only_without_pen_or_touch(engine, item_id, answer):
    engine.system.preconditions["no_pen_touch"] = answer
    row = next(r for r in engine.inspect({item_id}) if r.id == item_id)
    assert not row.offered and row.status == "Not applicable"
    engine.apply({item_id})
    assert not engine.system.writes


@pytest.mark.parametrize("answer", [False, None])
def test_smb_direct_needs_proof_of_no_rdma(engine, answer):
    engine.system.preconditions["no_rdma"] = answer
    row = next(r for r in engine.inspect({"smb_direct"}) if r.id == "smb_direct")
    assert not row.offered
    engine.apply({"smb_direct"})
    assert not engine.system.writes


@pytest.mark.parametrize("item_id", ["cap_steps", "work_folders", "snmp_trap"])
def test_absent_service_feature_or_capability_is_not_applicable(engine, item_id):
    engine.system.states[item_id] = {"exists": False}
    row = next(r for r in engine.inspect({item_id}) if r.id == item_id)
    assert not row.offered and row.status == "Not applicable"
    assert engine.apply({item_id}).skipped[item_id] == "Not applicable"
    assert not engine.system.writes


def test_capability_is_journaled_before_removal_and_restart_reported(engine):
    def check(spec, state):
        if state == {"exists": True, "installed": False}:
            entry = json.loads(engine.path.read_text())["items"][0]
            assert entry == {"id": "cap_steps", "before": {"exists": True, "installed": True}, "done": False}

    engine.system.before_write = check
    engine.system.restart.add("cap_steps")
    result = engine.apply({"cap_steps"})
    assert result.changed == ["cap_steps"] and result.restart == ["cap_steps"]
    assert "restart" in result.text()


def test_failed_capability_readd_stays_pending_with_its_error(engine):
    engine.apply({"cap_steps"})
    engine.system.fail_write.add("cap_steps")
    result = engine.revert()
    assert "cap_steps" in result.failed and "Windows Update" in result.failed["cap_steps"]
    assert "cap_steps" not in result.changed
    entry = json.loads(engine.path.read_text())["items"][0]
    assert entry["id"] == "cap_steps" and entry["before"]["installed"] is True
    assert "write refused" in entry["error"]
    assert engine.system.states["cap_steps"]["installed"] is False
    engine.system.fail_write.clear()
    assert engine.revert().changed == ["cap_steps"]
    assert not engine.path.exists()
    assert engine.system.states["cap_steps"]["installed"] is True


def test_capability_revert_warning_is_in_every_capability_why():
    for spec in opt.ITEMS:
        if spec.kind == "capability":
            assert "Windows Update / internet" in spec.why and "Insider" in spec.why


def test_never_offered_capabilities_are_not_items_and_are_refused_natively(monkeypatch):
    names = {s.target_name.casefold() for s in opt.ITEMS if s.kind == "capability"}
    for forbidden in ("hello.face.20134~~~~0.0.1.0", "language.basic~~~en-us~0.0.1.0",
                      "language.ocr~~~en-us~0.0.1.0", "language.speech~~~en-us~0.0.1.0",
                      "language.texttospeech~~~en-us~0.0.1.0", "microsoft.windows.powershell.ise~~~~0.0.1.0",
                      "onecoreuap.onesync~~~~0.0.1.0", "netfx3~~~~", "openssh.client~~~~0.0.1.0",
                      "microsoft.windows.notepad.system~~~~0.0.1.0", "microsoft.wallpapers.extended~~~~0.0.1.0",
                      "directx.configuration.database~~~~0.0.1.0",
                      "microsoft.windows.wifi.client.intel.netwtw10~~~~0.0.1.0",
                      "microsoft.windows.ethernet.client.realtek.rtcx21x64~~~~0.0.1.0",
                      "microsoft.windows.sense.client~~~~"):
        assert forbidden not in names
        assert any(forbidden.startswith(p) for p in opt.NEVER_OFFER)
    assert not any(n.startswith(p) for n in names for p in opt.NEVER_OFFER)
    calls = []
    monkeypatch.setattr(opt, "_powershell", lambda script, timeout: calls.append(script) or "")
    spec = opt.Spec("x", "x", "O", "capability", None, "x", admin=True, target_name="Hello.Face.20134~~~~0.0.1.0")
    with pytest.raises(ValueError):
        opt.WindowsSystem().write(spec, {"exists": True, "installed": False})
    assert not calls


def test_native_capability_read_and_write_go_through_one_listing(monkeypatch):
    scripts = []
    listing = "App.StepsRecorder~~~~0.0.1.0|Installed\nVBSCRIPT~~~~|UninstallPending\n"

    def fake(script, timeout):
        scripts.append(script)
        return "RESTART|True\n" if "Remove-WindowsCapability" in script else listing

    monkeypatch.setattr(opt, "_powershell", fake)
    system = opt.WindowsSystem()
    assert system.read(opt.BY_ID["cap_steps"]) == {"exists": True, "installed": True}
    assert system.read(opt.BY_ID["vbscript"]) == {"exists": True, "installed": "UninstallPending"}
    assert system.read(opt.BY_ID["cap_math"]) == {"exists": False}
    assert len(scripts) == 1                                   # cached listing
    outcome = system.write(opt.BY_ID["cap_steps"], {"exists": True, "installed": False})
    assert outcome == {"restart": True}
    assert "Remove-WindowsCapability -Online -Name 'App.StepsRecorder~~~~0.0.1.0'" in scripts[-1]
    system.read(opt.BY_ID["cap_steps"])
    assert "Get-WindowsCapability" in scripts[-1]              # a write drops the cache


def test_unknown_package_state_is_refused_not_guessed(monkeypatch):
    monkeypatch.setattr(opt, "_powershell", lambda script, timeout: "App.StepsRecorder~~~~0.0.1.0|Staged\n")
    with pytest.raises(OSError, match="not acted on"):
        opt.WindowsSystem().read(opt.BY_ID["cap_steps"])


def test_native_policy_revert_deletes_value_and_key_it_created(monkeypatch):
    import winreg

    calls = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *args: Key())
    monkeypatch.setattr(winreg, "DeleteValue", lambda key, name: calls.append(("value", name)))
    monkeypatch.setattr(winreg, "QueryInfoKey", lambda key: (0, 0, 0))
    monkeypatch.setattr(winreg, "DeleteKey", lambda hive, path: calls.append(("key", path)))
    opt.WindowsSystem().write(opt.BY_ID["brave_background"], {"exists": False, "key_exists": False})
    assert calls == [("value", "BackgroundModeEnabled"), ("key", r"SOFTWARE\Policies\BraveSoftware\Brave")]
    calls.clear()
    opt.WindowsSystem().write(opt.BY_ID["chrome_background"], {"exists": False, "key_exists": True})
    assert calls == [("value", "BackgroundModeEnabled")]       # an existing policy key stays


def test_read_only_notes(engine):
    notes = engine.notes()
    assert any("wisvc" in n and "Insider build" in n and "may not arrive" in n for n in notes)
    assert any("XboxGipSvc" in n and "controllers" in n for n in notes)
    engine.system.is_insider = False
    assert not any("wisvc" in n for n in engine.notes())
    engine.system.services["XboxGipSvc"]["start_type"] = 3
    assert not any("XboxGipSvc" in n for n in engine.notes())
    assert not engine.system.writes


# ---- Free up space -----------------------------------------------------------------------------

from modules.cleanup.cleanup_scanner import ScanItem, ScanResult  # noqa: E402
from modules.game_mode.engine import space as sp  # noqa: E402


class FakeReport:
    def __init__(self, freed=0, files=0):
        self.freed, self.files, self.locked, self.denied, self.cancelled = freed, files, [], [], False


class FakeBackend:
    def __init__(self):
        self.scanned = []
        self.deleted = []
        self.busy = (False, "")
        self.dism_runs = 0
        self.deleted_items = []
        self.measured = []
        self.unlistable = set()
        self.kids = {}
        self.results = {
            "scan_temp_files": [ScanItem(r"C:\T\a", 100, True, selected=False, keep_newer_than_hours=24)],
            "scan_wu_cache": [ScanItem(r"C:\Windows\SoftwareDistribution\Download", 400, True, safety="caution")],
            "scan_wer_reports": [ScanItem(r"C:\W\wer", 10, True, safety="caution")],
            "scan_user_crash_dumps": [ScanItem(r"C:\U\CrashDumps", 5, True, safety="caution")],
            "scan_minidump": [ScanItem(r"C:\Windows\Minidump", 7, True, safety="caution")],
            "scan_memory_dumps": [ScanItem(r"C:\Windows\Minidump\1.dmp", 7, False, safety="caution"),
                                  ScanItem(r"C:\Windows\MEMORY.DMP", 1000, False, safety="danger")],
            "scan_windows_logs": [ScanItem(r"C:\Windows\Logs\CBS\old.log", 3, False, safety="caution")],
            "scan_logviewer_leftovers": [],
            "scan_thumbnail_cache": [],
        }

    def scan(self, name, min_age_days):
        assert name in sp.SCANNERS
        self.scanned.append((name, min_age_days))
        return ScanResult(items=[copy.copy(i) for i in self.results[name]])

    def delete(self, items, stop_wuauserv, cancelled):
        self.deleted.append(([i.path for i in items], stop_wuauserv, [i.selected for i in items]))
        self.deleted_items.append(items)
        return FakeReport(sum(i.size for i in items), len(items)), [], []

    def children(self, path):
        self.measured.append(path)
        if path in self.unlistable:
            raise PermissionError("access denied")
        return list(self.kids.get(path, []))

    def file_newest(self, path):
        return NEWEST

    def measure(self, item, cancelled=lambda: False):
        where = sp.Location(item.path, item.size, item.is_dir, item.safety, item.keep_newer_than_hours)
        if not item.is_dir:
            where.newest = self.file_newest(item.path)
            return where
        try:
            kids = sorted(self.children(item.path), key=lambda e: (-e.size, e.path.lower()))
        except OSError as error:
            where.error = str(error)
            return where
        where.children, where.rest = kids[:sp.TOP_CHILDREN], kids[sp.TOP_CHILDREN:]
        stamps = [e.newest for e in kids if e.newest is not None]
        where.newest = max(stamps) if stamps else None
        return where

    def servicing_busy(self):
        return self.busy

    def delivery_size(self):
        return 0

    def delivery_clean(self):
        return {"returncode": 0}

    def dism(self, log, cancelled):
        self.dism_runs += 1
        return {"returncode": 0, "output": "ok"}


NEWEST = 1792584000.0          # 2026-10-21 12:00 UTC


def test_space_scans_only_the_listed_scanners_and_logs_age_floor():
    backend = FakeBackend()
    estimates = {e.id: e for e in sp.SpaceCleaner(backend).estimate()}
    assert {name for name, _age in backend.scanned} == set(sp.SCANNERS) == {
        "scan_temp_files", "scan_wu_cache", "scan_wer_reports", "scan_user_crash_dumps", "scan_minidump",
        "scan_memory_dumps", "scan_windows_logs", "scan_logviewer_leftovers", "scan_thumbnail_cache"}
    assert ("scan_windows_logs", 30) in backend.scanned
    assert estimates["dumps"].size == 12                       # nested .dmp not counted twice; danger left out
    assert estimates["wu_cache"].size == 400


def test_space_deletes_only_listed_items_with_wu_stopped_separately():
    backend = FakeBackend()
    result = sp.SpaceCleaner(backend).clean({"temp", "wu_cache", "dumps"})
    assert [(sorted(paths), stop, selected) for paths, stop, selected in backend.deleted] == [
        (sorted([r"C:\T\a", r"C:\Windows\Minidump", r"C:\U\CrashDumps"]), False, [True, True, True]),
        ([r"C:\Windows\SoftwareDistribution\Download"], True, [True])]
    assert (r"C:\Windows\MEMORY.DMP", "marked dangerous by its scanner -- not part of this cleanup") in result.refused
    assert result.freed == 512
    assert backend.dism_runs == 0
    assert "Measured" in result.text()


@pytest.mark.parametrize("busy", [(True, "Windows Update is installing an update"), (None, "unreadable")])
def test_wu_cache_refused_but_dism_coordinates_its_own_lock(busy):
    backend = FakeBackend()
    backend.busy = busy
    result = sp.SpaceCleaner(backend).clean({"wu_cache", sp.DISM_ID, "wer"})
    assert set(result.skipped) == {"wu_cache"}
    assert busy[1] in result.skipped["wu_cache"]
    assert backend.dism_runs == 1
    assert backend.deleted == [([r"C:\W\wer"], False, [True])]


def test_dism_runs_only_when_servicing_is_idle():
    backend = FakeBackend()
    result = sp.SpaceCleaner(backend).clean({sp.DISM_ID})
    assert backend.dism_runs == 1 and result.dism["returncode"] == 0
    assert not backend.deleted
    assert "ResetBase" in sp.DISM_NOTE and "uninstallable" in sp.DISM_NOTE


def test_real_backend_never_uses_the_safe_stage_and_deletes_through_delete_items_report(monkeypatch):
    from modules.cleanup.cleanup_scanner import scan_cache, scanners_system
    from modules.updates import stage_runners

    fake = FakeBackend()
    scanned, deleted = [], []

    def cached(fn, min_age_days=0, ttl_seconds=60):
        scanned.append(fn.__name__)
        return fake.scan(fn.__name__, min_age_days)

    def report(items, on_progress=None, stop_wuauserv=False, cancelled=lambda: False):
        deleted.append(([i.path for i in items], stop_wuauserv))
        return FakeReport(1, 1), [], []

    def forbidden(*args, **kwargs):
        raise AssertionError("run_cleanup_safe_stage must never be called")

    monkeypatch.setattr(scan_cache, "cached_scan", cached)
    monkeypatch.setattr(scanners_system, "delete_items_report", report)
    monkeypatch.setattr(stage_runners, "run_cleanup_safe_stage", forbidden)
    dism_calls = []
    monkeypatch.setattr(stage_runners, "run_dism_stage", lambda app, log, cancelled: dism_calls.append(1) or {"returncode": 0})
    backend = sp.CleanupBackend()
    monkeypatch.setattr(sp, "scan_logviewer_leftovers", lambda: fake.scan("scan_logviewer_leftovers", 0))
    monkeypatch.setattr(backend, "delivery_clean", lambda: {"returncode": 0})
    monkeypatch.setattr(backend, "servicing_busy", lambda: (False, ""))
    cleaner = sp.SpaceCleaner(backend)
    cleaner.estimate()
    result = cleaner.clean({g.id for g in sp.GROUPS} | {sp.DISM_ID})
    assert set(scanned) == set(sp.SCANNERS) - {"scan_logviewer_leftovers"}
    assert [paths for paths, _stop in deleted][0] and all(
        p in {i.path for items in fake.results.values() for i in items} for paths, _ in deleted for p in paths)
    assert [stop for _paths, stop in deleted] == [False, True]
    assert dism_calls == [1] and result.dism == {"returncode": 0}
    with pytest.raises(ValueError):
        backend.scan("scan_recycle_bin", 0)


def test_exclusions_are_stated():
    names = " ".join(name for name, _why in sp.EXCLUDED)
    for excluded in ("Shader caches", "Recycle Bin", "Restore points", "Browser data"):
        assert excluded in names
    assert "Revert does not bring them back" in sp.ONE_WAY_NOTE


# ---- Apps you may not use ----------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402
import struct  # noqa: E402

from modules.game_mode.engine import app_usage as au  # noqa: E402

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _ft(when):
    return int((when - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)


def _ua(when, count=3):
    data = bytearray(72)
    struct.pack_into("<I", data, 4, count)
    struct.pack_into("<Q", data, 60, _ft(when) if when else 0)
    return bytes(data)


def test_userassist_decodes_rot13_and_skips_control_records_and_bad_dates():
    records = [
        (au.rot13("UEME_CTLSESSION"), _ua(NOW - timedelta(days=1))),
        (au.rot13(r"C:\Tools\Old\old.exe"), _ua(NOW - timedelta(days=200))),
        (au.rot13(r"C:\Tools\Bad\bad.exe"), _ua(None)),
        (au.rot13(r"C:\Tools\Ancient\x.exe"), _ua(datetime(1641, 2, 20, tzinfo=timezone.utc))),
        (au.rot13("Microsoft.Paint_8wekyb3d8bbwe!App"), _ua(NOW - timedelta(days=2))),
    ]
    found = au.userassist_evidence(records, NOW)
    assert [(e.key, e.source) for e in found] == [
        ("path:" + au._norm(r"C:\Tools\Old\old.exe"), "UserAssist"),
        ("family:microsoft.paint_8wekyb3d8bbwe", "UserAssist")]


def test_bam_translates_device_paths():
    data = struct.pack("<Q", _ft(NOW - timedelta(days=3))) + bytes(16)
    found = au.bam_evidence([(r"\Device\HarddiskVolume3\Apps\Tool\tool.exe", data), ("Version", 1)],
                            {r"\device\harddiskvolume3": "C:"}, NOW)
    assert found[0].key == "path:" + au._norm(r"C:\Apps\Tool\tool.exe")


def _usage(evidence, desktop=(), appx=()):
    return au.analyse(evidence, list(desktop), list(appx), NOW)


def test_three_states_and_no_record_is_never_unused():
    evidence = [au.Evidence("path:" + au._norm(r"C:\Apps\Fresh\fresh.exe"), NOW - timedelta(days=5), "BAM"),
                au.Evidence("path:" + au._norm(r"C:\Apps\Old\bin\old.exe"), NOW - timedelta(days=120), "UserAssist")]
    desktop = [{"name": "Fresh", "install_location": r"C:\Apps\Fresh"},
               {"name": "Old", "install_location": r"C:\Apps\Old"},
               {"name": "Never", "install_location": r"C:\Apps\Never"},
               {"name": "Nowhere"}]
    report = _usage(evidence, desktop)
    states = {a.name: a.state for a in report.apps}
    assert states == {"Fresh": au.USED, "Old": au.STALE, "Never": au.UNKNOWN, "Nowhere": au.UNKNOWN}
    assert [a.name for a in report.suggestions] == ["Old"]
    fresh = next(a for a in report.apps if a.name == "Fresh")
    assert fresh.source == "BAM" and fresh.last == NOW - timedelta(days=5)
    assert report.reach_text() == f"Usage records only go back to {(NOW - timedelta(days=120)):%Y-%m-%d}."
    assert "never called unused" in report.summary()


def test_zero_suggestions_is_said_honestly():
    report = _usage([au.Evidence("family:a.b_1234567890abc", NOW - timedelta(days=47), "BAM")],
                    [{"name": "Unknown thing", "install_location": r"C:\Apps\U"}])
    assert not report.suggestions
    assert report.summary().startswith("No suggestions")
    assert "records only go back to 2026-09-04" in report.summary()


@pytest.mark.parametrize("name,publisher,location", [
    ("Microsoft Office Professional", "Microsoft", r"C:\Apps\Office"), ("Microsoft OneNote", "", r"C:\Apps\ON"),
    ("Microsoft OneDrive", "", r"C:\Apps\OD"), ("Microsoft Teams", "", r"C:\Apps\Teams"),
    ("Word Viewer", "", r"C:\Apps\Word"), ("Excel Tools", "", r"C:\Apps\Excel"), ("Google Chrome", "", r"C:\Apps\GC"),
    ("Brave", "", r"C:\Apps\Brave"), ("Stremio", "", r"C:\Apps\Stremio"), ("Ollama", "", r"C:\Apps\Ollama"),
    ("eSpeak NG", "", r"C:\Apps\eSpeak"), ("Sysmon", "", r"C:\Apps\Sysmon"),
    ("Microsoft Visual Studio Code", "", r"C:\Apps\VSC"), ("Python 3.12.4", "", r"C:\Apps\Py"),
    ("Git", "The Git Development Community", r"C:\Apps\Git"), ("Codex CLI", "", r"C:\Apps\Codex"),
    ("Claude", "Anthropic", r"C:\Apps\Claude"), ("Bitdefender Total Security", "", r"C:\Apps\BD"),
    ("CyberGhost", "", r"C:\Apps\CG"), ("AMD Chipset Software", "Advanced Micro Devices", r"C:\Apps\AMD"),
    ("Realtek Audio Driver", "Realtek", r"C:\Apps\RT"), ("Microsoft Visual C++ 2015 Redistributable", "", r"C:\Apps\VC"),
    ("Microsoft Windows Desktop Runtime", "", r"C:\Apps\RT2"), ("Steam", "Valve", r"C:\Apps\Steam"),
    ("Battle.net", "Blizzard", r"C:\Apps\BN"), ("Some Game", "", r"D:\SteamLibrary\steamapps\common\Some Game"),
])
def test_protected_programs_are_never_suggested(name, publisher, location):
    evidence = [au.Evidence("path:" + au._norm(location + r"\app.exe"), NOW - timedelta(days=400), "UserAssist")]
    report = _usage(evidence, [{"name": name, "publisher": publisher, "install_location": location}])
    assert not report.apps and not report.suggestions
    assert report.protected == 1


def test_appx_matching_by_family_skips_plumbing_and_games():
    appx = [{"full_name": "Vendor.Thing_1.0.0.0_x64__abcdefghijklm",
             "root": r"C:\Program Files\WindowsApps\Vendor.Thing_1.0.0.0_x64__abcdefghijklm", "display": "Thing"},
            {"full_name": "Microsoft.VCLibs.140.00_14.0_x64__8wekyb3d8bbwe",
             "root": r"C:\Program Files\WindowsApps\Microsoft.VCLibs", "display": ""},
            {"full_name": "Studio.Game_1.0_x64__abcdefghijklm",
             "root": r"C:\Program Files\WindowsApps\Studio.Game", "display": "A game", "game": True},
            {"full_name": "Microsoft.Windows.Thing_1.0_neutral__cw5n1h2txyewy",
             "root": r"C:\Windows\SystemApps\Thing", "display": ""}]
    evidence = [au.Evidence("family:vendor.thing_abcdefghijklm", NOW - timedelta(days=200), "BAM")]
    report = _usage(evidence, appx=appx)
    assert [(a.name, a.state) for a in report.apps] == [("Thing", au.STALE)]
    assert report.protected == 1


def test_usage_reader_reports_unreadable_sources_and_still_answers():
    class Backend:
        def userassist(self):
            return []

        def bam(self):
            raise OSError("needs administrator")

        def devices(self):
            return {}

        def prefetch(self):
            return []

        def uninstall(self):
            return [{"name": "Tool", "install_location": r"C:\Apps\Tool"}]

        def appx(self):
            return []

    report = au.UsageReader(Backend()).analyse(NOW)
    assert report.apps[0].state == au.UNKNOWN
    assert any("BAM" in e for e in report.errors)
    assert "No usage records could be read." in report.summary()


# ---- Tab ---------------------------------------------------------------------------------------

class _Pool:
    def __init__(self):
        self.queue = []

    def start(self, worker):
        self.queue.append(worker)

    def drain(self):
        while self.queue:
            self.queue.pop(0).run()


class _FakeUsage:
    def analyse(self):
        return _usage([au.Evidence("family:x_abcdefghijklm", datetime(2026, 9, 4, tzinfo=timezone.utc), "BAM")],
                      [{"name": "Tool", "install_location": r"C:\Apps\Tool"}])


def test_tab_shows_estimates_usage_and_notes_and_opens_app_buster(tmp_path, qapp):
    from modules.game_mode.optimise_tab import OptimiseTab

    backend = FakeBackend()
    engine = opt.Optimiser(FakeSystem(), str(tmp_path), space=sp.SpaceCleaner(backend), usage=_FakeUsage())
    pool, navigated = _Pool(), []
    tab = OptimiseTab(pool, optimiser=engine, navigate=navigated.append)
    tab.refresh()
    pool.drain()
    wu = _group_row(tab, "wu_cache")
    assert wu.text(1) == "400 B" and wu.child(0).text(0) == r"C:\Windows\SoftwareDistribution\Download"
    assert tab._usage_summary.text().startswith("No suggestions")
    assert "records only go back to 2026-09-04" in tab._usage_summary.text()
    assert "never called unused" in tab._usage_summary.text()
    assert tab._usage_tree.topLevelItemCount() == 1
    assert "unknown, not unused" in tab._usage_tree.topLevelItem(0).text(2)
    assert "wisvc" in tab._observed.text() and "XboxGipSvc" in tab._observed.text()
    tab._buster_btn.click()
    assert navigated == ["App Buster"]
    assert not backend.deleted and not engine.system.writes
    tab.close()


def test_space_confirmation_is_separate_and_cancel_deletes_nothing(tmp_path, qapp, monkeypatch):
    from modules.game_mode.optimise_tab import OptimiseTab

    backend = FakeBackend()
    engine = opt.Optimiser(FakeSystem(), str(tmp_path), space=sp.SpaceCleaner(backend), usage=None)
    pool = _Pool()
    tab = OptimiseTab(pool, optimiser=engine)
    tab.refresh()
    pool.drain()
    _group_row(tab, "temp").setCheckState(0, Qt.CheckState.Checked)
    texts = []
    monkeypatch.setattr(tab, "_confirm", lambda title, lead, text: texts.append((title, lead, text)) or False)
    tab._preview_space()
    assert texts and "One-way" in texts[0][1] and "Temporary files" in texts[0][2]
    assert not pool.queue and not backend.deleted
    monkeypatch.setattr(tab, "_confirm", lambda title, lead, text: True)
    tab._preview_space()
    pool.drain()
    assert backend.deleted and backend.deleted[0][0] == [r"C:\T\a"]
    assert "Measured" in tab._space_result.toPlainText()
    assert not engine.path.exists()                             # one-way: never journaled
    tab.close()


# ---- Free up space: locations, partial selection, confirmation --------------------------------

from PyQt6.QtCore import Qt  # noqa: E402


def _group_row(tab, gid):
    tree = tab._space_tree
    return next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == ("group", gid))


def _temp_backend(count=17):
    backend = FakeBackend()
    backend.kids[r"C:\T\a"] = [sp.Entry(rf"C:\T\a\dir{i:02}", (i + 1) * 1000, True, NEWEST - i * 86400)
                               for i in range(count - 1)] + [sp.Entry(r"C:\T\a\loose.bin", 5, False, NEWEST)]
    return backend


def _space_tab(tmp_path, backend):
    from modules.game_mode.optimise_tab import OptimiseTab
    engine = opt.Optimiser(FakeSystem(), str(tmp_path), space=sp.SpaceCleaner(backend), usage=None)
    pool = _Pool()
    tab = OptimiseTab(pool, optimiser=engine)
    return tab, pool


def test_breakdown_lists_items_and_their_largest_top_level_entries():
    backend = _temp_backend()
    found = sp.SpaceCleaner(backend).breakdown()
    temp = found["temp"]
    assert [w.path for w in temp] == [r"C:\T\a"]
    assert [e.path for e in temp[0].children][:2] == [r"C:\T\a\dir15", r"C:\T\a\dir14"]
    assert len(temp[0].children) == sp.TOP_CHILDREN
    assert {e.path for e in temp[0].rest} == {r"C:\T\a\dir00", r"C:\T\a\loose.bin"}
    assert temp[0].newest == NEWEST and temp[0].keep_newer_than_hours == 24
    assert {w.path for w in found["dumps"]} == {r"C:\Windows\MEMORY.DMP", r"C:\U\CrashDumps",
                                                r"C:\Windows\Minidump"}
    assert not next(w for w in found["dumps"] if w.path.endswith("MEMORY.DMP")).deletable
    assert not backend.deleted


def test_breakdown_reports_an_unlistable_folder_instead_of_an_empty_one():
    backend = _temp_backend()
    backend.unlistable.add(r"C:\T\a")
    where = sp.SpaceCleaner(backend).breakdown()["temp"][0]
    assert "access denied" in where.error and not where.children


def test_tab_measures_locations_only_on_the_worker_and_shows_rows(tmp_path, qapp):
    backend = _temp_backend()
    tab, pool = _space_tab(tmp_path, backend)
    tab.refresh()
    assert not backend.measured and not backend.scanned          # nothing measured on the UI thread
    pool.drain()
    assert r"C:\T\a" in backend.measured
    item = _group_row(tab, "temp").child(0)
    assert item.text(0) == r"C:\T\a" and item.text(2) == "newest 2026-10-21" and item.text(3) == "safe"
    assert "24 hours are kept" in item.toolTip(0)
    assert item.childCount() == sp.TOP_CHILDREN + 1
    assert item.child(0).text(0) == "dir15\\"
    assert item.child(sp.TOP_CHILDREN).text(0).startswith("2 more (")
    dumps = _group_row(tab, "dumps")
    danger = next(dumps.child(i) for i in range(dumps.childCount()) if dumps.child(i).text(0).endswith("MEMORY.DMP"))
    assert not danger.flags() & Qt.ItemFlag.ItemIsUserCheckable
    tab.close()


def _tick_all_but(tab, unticked):
    item = _group_row(tab, "temp").child(0)
    item.setCheckState(0, Qt.CheckState.Checked)
    for i in range(item.childCount()):
        if item.child(i).text(0) in unticked:
            item.child(i).setCheckState(0, Qt.CheckState.Unchecked)
    return item


def test_unticked_subfolder_is_excluded_from_the_delete_call(tmp_path, qapp, monkeypatch):
    backend = _temp_backend()
    tab, pool = _space_tab(tmp_path, backend)
    tab.refresh()
    pool.drain()
    _tick_all_but(tab, {"dir15\\"})
    texts = []
    monkeypatch.setattr(tab, "_confirm", lambda title, lead, text: texts.append(text) or True)
    tab._preview_space()
    pool.drain()
    paths = backend.deleted[0][0]
    assert r"C:\T\a\dir15" not in paths and r"C:\T\a" not in paths
    assert set(paths) == {rf"C:\T\a\dir{i:02}" for i in range(15)} | {r"C:\T\a\loose.bin"}
    items = {i.path: i for i in backend.deleted_items[0]}
    assert items[r"C:\T\a\dir03"].is_dir and not items[r"C:\T\a\dir03"].keep_root
    assert items[r"C:\T\a\loose.bin"].keep_newer_than_hours == 24       # the 24-hour rule still applies
    assert r"C:\T\a\dir15" not in texts[0] and r"C:\T\a\dir14 -- " in texts[0]
    tab.close()


def test_partial_selection_deletes_only_the_ticked_entries_and_confirms_their_total(tmp_path, qapp, monkeypatch):
    from core.formatting import format_size

    backend = _temp_backend()
    tab, pool = _space_tab(tmp_path, backend)
    tab.refresh()
    pool.drain()
    item = _group_row(tab, "temp").child(0)
    item.child(0).setCheckState(0, Qt.CheckState.Checked)                # dir15, 16,000 B
    item.child(sp.TOP_CHILDREN).setCheckState(0, Qt.CheckState.Checked)  # "2 more": dir00 + loose.bin
    assert item.checkState(0) == Qt.CheckState.PartiallyChecked
    texts = []
    monkeypatch.setattr(tab, "_confirm", lambda title, lead, text: texts.append(text) or True)
    tab._preview_space()
    pool.drain()
    assert sorted(backend.deleted[0][0]) == [r"C:\T\a\dir00", r"C:\T\a\dir15", r"C:\T\a\loose.bin"]
    assert f"Total: {format_size(16000 + 1000 + 5)} in 3 location(s)" in texts[0]
    for path in (r"C:\T\a\dir15", r"C:\T\a\dir00", r"C:\T\a\loose.bin"):
        assert path in texts[0]
    assert "Excluded:" in texts[0]
    tab.close()


def test_whole_folder_ticked_is_cleaned_as_one_item(tmp_path, qapp, monkeypatch):
    backend = _temp_backend()
    tab, pool = _space_tab(tmp_path, backend)
    tab.refresh()
    pool.drain()
    _group_row(tab, "temp").setCheckState(0, Qt.CheckState.Checked)
    monkeypatch.setattr(tab, "_confirm", lambda title, lead, text: True)
    tab._preview_space()
    pool.drain()
    assert backend.deleted[0][0] == [r"C:\T\a"]
    tab.close()


def test_clean_refuses_a_child_that_is_not_inside_its_location():
    backend = _temp_backend()
    result = sp.SpaceCleaner(backend).clean({"temp"}, choice={"temp": {r"C:\T\a": [(r"C:\Elsewhere\x", True)]}})
    assert not backend.deleted
    assert result.refused == [(r"C:\Elsewhere\x", "not directly inside the location it was listed under")]


def test_nothing_ticked_deletes_nothing(tmp_path, qapp, monkeypatch):
    backend = _temp_backend()
    tab, pool = _space_tab(tmp_path, backend)
    tab.refresh()
    pool.drain()
    monkeypatch.setattr(tab, "_confirm", lambda *args: pytest.fail("nothing to confirm"))
    tab._preview_space()
    assert "Tick at least one" in tab._space_result.toPlainText() and not pool.queue
    tab.close()


# ---- Confirmation dialog -----------------------------------------------------------------------

def test_confirm_dialog_is_resizable_scrollable_and_fits_the_screen(qapp):
    from PyQt6.QtWidgets import QWidget
    from modules.game_mode.confirm_dialog import SCREEN_SHARE, ConfirmDialog

    host = QWidget()
    details = "\n\n".join(f"Setting {i}: before -> after\n  C:\\Some\\Path\\{i} -- 1.0 GB" for i in range(400))
    dialog = ConfirmDialog(host, "Apply", "Apply exactly these changes?", details)
    area = (host.screen() or dialog.screen()).availableGeometry()
    assert dialog.isSizeGripEnabled()
    assert dialog.width() <= int(area.width() * SCREEN_SHARE) and dialog.height() <= int(area.height() * SCREEN_SHARE)
    assert dialog.maximumWidth() <= area.width() and dialog.maximumHeight() <= area.height()
    assert dialog.details.isReadOnly() and "Setting 399" in dialog.details.toPlainText()
    assert dialog.cancel_button.isDefault()
    dialog.deleteLater()
    host.deleteLater()


def test_confirm_dialog_escapes_html():
    from modules.game_mode.confirm_dialog import details_html
    assert "&lt;b&gt;" in details_html("<b>not bold</b>") and "<b>" not in details_html("<b>x</b>")
