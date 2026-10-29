"""Game Mode extended engines: library, control, audit, history, metrics, guards."""
import json
from datetime import datetime
from types import SimpleNamespace

from modules.game_mode.engine import (game_library, health_audit, history_export,
                                      metrics, preflight_guard, session_control)


def test_library_finds_steam_and_epic_games(tmp_path):
    steam = tmp_path / "Steam"
    common = steam / "steamapps" / "common" / "Cool Game"
    common.mkdir(parents=True)
    (common / "cool.exe").write_bytes(b"x")
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "' + str(steam).replace("\\", "\\\\") + '" } }',
        encoding="utf-8")
    manifests = tmp_path / "Epic"
    manifests.mkdir()
    install = tmp_path / "EpicGame"
    install.mkdir()
    (install / "run.exe").write_bytes(b"x")
    (manifests / "a.item").write_text(json.dumps({
        "DisplayName": "Epic Game", "InstallLocation": str(install),
        "LaunchExecutable": "run.exe"}), encoding="utf-8")
    games = game_library.scan_games(str(steam), str(manifests))
    names = {g.name for g in games}
    assert "Cool Game" in names and "Epic Game" in names
    assert all(g.exe for g in games)


def test_running_game_matches_name_or_path():
    procs = [SimpleNamespace(name="wow.exe", exe=r"C:\Games\Wow\wow.exe")]
    assert game_library.running_game("wow.exe", procs)
    assert game_library.running_game(r"C:\Games\Wow\wow.exe", procs)
    assert not game_library.running_game("other.exe", procs)


def test_auto_off_honours_limit_and_game_exit_grace():
    assert session_control.should_auto_off(started=0, now=50, minutes=1) == (False, "")
    off, why = session_control.should_auto_off(started=0, now=61, minutes=1)
    assert off and "limit" in why
    off, why = session_control.should_auto_off(
        started=0, now=1000, minutes=0, game_alive=False, last_alive=600, grace_s=300)
    assert off and "game" in why
    assert not session_control.should_auto_off(
        started=0, now=1000, minutes=0, game_alive=True, last_alive=600)[0]


def test_schedule_window_is_weekday_and_time_aware():
    schedule = {"days": [2], "start": "20:00", "end": "23:00", "profile": "Raid"}
    at = datetime(2026, 10, 28, 21, 0)  # Wednesday, weekday 2
    assert session_control.schedule_for(at, [schedule]) is schedule
    assert session_control.schedule_for(at.replace(hour=19), [schedule]) is None


def test_pause_and_priority_helpers():
    pause = session_control.PauseState(until=1000)
    assert pause.paused(999) and not pause.paused(1000)
    assert session_control.priority_value("above") == session_control.ABOVE_NORMAL
    assert session_control.priority_value("high") == session_control.HIGH
    assert session_control.priority_value("off") is None


class _AuditSystem:
    def __init__(self):
        self.services = {"svc": SimpleNamespace(running=False, error="")}
        self.tasks = {"\\Task": False}
        self.plan = "plan-after"
        self.reg = {"value": 1}
        self.repairs = []

    def service(self, name):
        return self.services.get(name)

    def task_enabled(self, path):
        return self.tasks.get(path)

    def active_plan(self):
        return self.plan

    def reg_get(self, path, name, hive="HKCU"):
        return (True, self.reg.get(name), 4)

    def stop_service(self, name):
        self.repairs.append(("service", name))
        return None

    def set_task_enabled(self, path, enabled):
        self.repairs.append(("task", path))
        return None

    def set_plan(self, plan):
        self.repairs.append(("plan", plan))
        return None

    def reg_set(self, path, name, value, kind, hive="HKCU"):
        self.repairs.append(("reg", name))
        return None


def test_audit_reports_ok_drift_and_unknown_and_repairs_drift():
    system = _AuditSystem()
    actions = [
        {"kind": "service_stop", "name": "svc", "label": "Svc", "done": True},
        {"kind": "plan", "after": "plan-after", "done": True},
        {"kind": "registry", "path": "p", "name": "value", "after": 2, "type": 4,
         "done": True},
        {"kind": "task", "path": "\\Task", "label": "T", "done": True},
    ]
    rows = health_audit.audit(system, actions)
    assert [r["status"] for r in rows] == ["ok", "ok", "drift", "ok"]
    health_audit.repair(system, rows)
    assert system.repairs == [("reg", "value")]


def test_history_filter_aggregate_note_and_trim():
    entries = [
        {"started": 1000, "duration_s": 60, "preset": "Raid", "restore_ok": True,
         "restore_issues": []},
        {"started": 2000, "duration_s": 120, "preset": "Raid", "restore_ok": False,
         "restore_restored": 0, "restore_issues": ["X failed"]},
        {"started": 3000, "duration_s": 30, "preset": "Max", "restore_ok": None,
         "restore_issues": []},
    ]
    assert len(history_export.filter_sessions(entries, preset="Raid")) == 2
    assert len(history_export.filter_sessions(entries, restore="Failed")) == 1
    stats = history_export.aggregates(entries)
    assert stats["sessions"] == 3 and stats["by_preset"]["Raid"] == 2
    text = history_export.to_csv(entries)
    assert "Raid" in text and "X failed" in text
    noted = history_export.set_note(entries, 2000, "Raid night")
    assert noted[1]["note"] == "Raid night"
    assert len(history_export.trim(entries, 2)) == 2


def test_metrics_snapshot_survives_missing_psutil():
    class S:
        def processes(self):
            return [SimpleNamespace(handles=10), SimpleNamespace(handles=20)]

    data = metrics.snapshot(S(), psutil_module=SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(percent=40, used=100, total=1000),
        cpu_percent=lambda interval=None: 12))
    assert data["handles"] == 30 and data["processes"] == 2
    assert data["ram_percent"] == 40.0 and data["cpu_percent"] == 12.0


def test_preflight_guard_names_every_conflict():
    rows = preflight_guard.conflicts(
        pending_reboot=True, reboot_reasons=["Windows Update"], wu_installing=True,
        owner="Game Mode ON", maintenance_active=True)
    text = " ".join(rows)
    assert "restart" in text and "Windows Update" in text
    assert "Another operation" in text and "Maintenance" in text
    assert preflight_guard.conflicts() == []


def test_affinity_mask_splits_the_two_ccds():
    assert session_control.affinity_mask("off", 32) is None
    assert session_control.affinity_mask("ccd0", 32) == 0x0000FFFF
    assert session_control.affinity_mask("ccd1", 32) == 0xFFFF0000


def test_multimedia_and_game_tuning_are_journaled(tmp_path):
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal
    from modules.game_mode.engine.system import Proc

    class TuneSystem:
        def __init__(self):
            self.reg = {}
            self.affinity = 0xFF
            self.throttled = True
            self.p = Proc(7, 1, "game.exe", 100, 1, 50)

        def processes(self):
            return [self.p]

        def details(self, pid):
            return (r"C:\Games\game.exe", r"C:\Games\game.exe", False)

        def priority(self, pid):
            return 0x20

        def set_priority(self, pid, value):
            return None

        def get_affinity(self, pid):
            return self.affinity

        def set_affinity(self, pid, mask):
            self.affinity = mask
            return None

        def power_throttling(self, pid):
            return self.throttled

        def set_power_throttling(self, pid, value):
            self.throttled = value
            return None

        def reg_get(self, path, name, hive="HKCU"):
            return (False, None, 4)

        def reg_set(self, path, name, value, kind, hive="HKCU"):
            self.reg[(path, name)] = value
            return None

    system = TuneSystem()
    journal = Journal(str(tmp_path))
    journal.begin()
    on = session._On(system, journal, lambda _l, _t: None)
    on.multimedia(26, 10)
    on.tune_game(r"C:\Games\game.exe", "ccd0", True, 32)
    kinds = [entry["kind"] for entry in journal.actions]
    assert "registry" in kinds and "affinity" in kinds and "power_throttling" in kinds
    assert system.affinity == 0xFFFF
    assert system.throttled is False
    assert system.reg
