"""Regression cases for the verified Optimise OS review findings."""
import copy
import json

import pytest

from modules.game_mode.engine import optimise as opt
from modules.game_mode.engine.journal import Journal
from test_optimise_os import FakeSystem


def test_revert_defers_game_mode_hold_but_restores_other_entries(tmp_path):
    engine = opt.Optimiser(FakeSystem(), str(tmp_path))
    engine.apply({"sysmain", "menu_delay"})
    held = copy.deepcopy(engine.system.states["sysmain"])
    journal = Journal(str(tmp_path))
    journal.record({"kind": "service_stop", "name": "SysMain"})
    engine.system.writes.clear()
    result = engine.revert()
    assert engine.system.states["sysmain"] == held
    assert [key for key, _ in engine.system.writes] == ["menu_delay"]
    assert "held by Game Mode; revert after Game Mode OFF" in result.skipped["sysmain"]
    entries = json.loads(engine.path.read_text())["items"]
    assert [e["id"] for e in entries] == ["sysmain"]
    assert "held by Game Mode" in entries[0]["error"]
    assert "held by Game Mode" in result.text()


@pytest.mark.parametrize("kind,item_id,raw", [
    ("feature", "work_folders", "EnablePending"),
    ("feature", "work_folders", "DisablePending"),
    ("capability", "cap_steps", "InstallPending"),
    ("capability", "cap_steps", "UninstallPending"),
])
def test_pending_packages_are_preserved_and_wait_for_restart(tmp_path, monkeypatch, kind, item_id, raw):
    native = opt.WindowsSystem()
    spec = opt.BY_ID[item_id]
    monkeypatch.setattr(native, "_states", lambda _kind: {spec.target_name.casefold(): raw})
    state = spec.read(native)
    flag = "enabled" if kind == "feature" else "installed"
    assert state[flag] == raw
    assert opt.display(state) == "Pending restart"
    engine = opt.Optimiser(FakeSystem(), str(tmp_path))
    engine.system.states[item_id] = state
    row = next(r for r in engine.inspect({item_id}) if r.id == item_id)
    assert row.status == "Pending restart"
    assert not row.offered
    result = engine.apply({item_id})
    assert "waiting for restart" in result.skipped[item_id]
    assert not engine.system.writes and not engine.path.exists()
    engine.system.states[item_id] = {"exists": True, flag: True}
    engine.apply({item_id})
    engine.system.states[item_id] = state
    engine.system.writes.clear()
    result = engine.revert()
    assert "waiting for restart" in result.skipped[item_id]
    assert not engine.system.writes
    assert json.loads(engine.path.read_text())["items"][0]["id"] == item_id


@pytest.mark.parametrize("kind", ["apply", "revert"])
def test_accepted_confirmation_starts_action_when_preview_finished_inside_dialog(tmp_path, qapp, monkeypatch, kind):
    from modules.game_mode.optimise_tab import OptimiseTab

    queue = []

    class Pool:
        def start(self, worker):
            queue.append(worker)

    engine = opt.Optimiser(FakeSystem(), str(tmp_path))
    if kind == "revert":
        engine.apply({"menu_delay"})
    tab = OptimiseTab(Pool(), optimiser=engine)

    def confirm(*_args):
        tab._worker.signals.finished.emit()
        assert tab._worker is None
        return True

    monkeypatch.setattr(tab, "_confirm", confirm)
    try:
        getattr(tab, "_preview_" + kind)()
        queue.pop(0).run()
        assert len(queue) == 1, "accepted action must be queued even after nested finished"
        assert tab._pending is None
        queue.pop(0).run()
        assert engine.system.states["menu_delay"]["value"] == ("0" if kind == "apply" else "200")
        while queue:
            queue.pop(0).run()
        assert not tab.busy
    finally:
        tab.cancel()
        tab.close()
