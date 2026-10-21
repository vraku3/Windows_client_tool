"""Task presets and Photos preloading, without changing the real machine."""
from unittest.mock import Mock

import pytest

from modules.game_mode.engine import catalog, session
from modules.game_mode.engine.system import Proc, WinSystem
from test_game_mode import FakeSystem


class TaskSystem(FakeSystem):
    def __init__(self):
        super().__init__()
        self.running_tasks = set()
        self.stopped_tasks = []
        self.task_writes = []

    def task_running(self, path):
        return path in self.running_tasks

    def stop_task(self, path):
        self.stopped_tasks.append(path)
        self.running_tasks.discard(path)

    def set_task_enabled(self, path, enabled):
        self.task_writes.append((path, enabled))
        return super().set_task_enabled(path, enabled)


@pytest.fixture
def journal(tmp_path):
    from modules.game_mode.engine.journal import Journal
    return Journal(str(tmp_path))


@pytest.mark.parametrize("level,expected", [(catalog.REC, {r"\Rec"}),
                                           (catalog.MAX, {r"\Rec", r"\Max"})])
def test_task_presets_and_exact_restore(monkeypatch, journal, level, expected):
    specs = tuple(catalog.TaskSpec(path, path, kind) for path, kind in
                  [(r"\Rec", catalog.REC), (r"\Max", catalog.MAX), (r"\Opt", catalog.OPT),
                   (r"\AlreadyDisabled", catalog.REC), (r"\Refused", catalog.REC)])
    monkeypatch.setattr(catalog, "TASKS", specs)
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    s = TaskSystem()
    s.task_state = {spec.pattern: spec.pattern != r"\AlreadyDisabled" for spec in specs}
    s.task_state[r"\Uncatalogued"] = True
    s.protected = {r"\Refused"}
    s.running_tasks = {r"\Rec", r"\Max", r"\Refused", r"\AlreadyDisabled"}
    before = dict(s.task_state)
    log = Mock()
    choices = session.preset(level, [])
    data = session.turn_on(s, choices, journal, log, 999, 1)
    assert {p for p, enabled in s.task_state.items() if before[p] and not enabled} == expected
    assert set(s.stopped_tasks) == (expected if level == catalog.MAX else set())
    if level == catalog.MAX:
        assert any("stopped a run in progress; it runs again at its next trigger" in call.args[1]
                   for call in log.call_args_list)
    assert {e["path"] for e in data["actions"] if e["kind"] == "task" and e["done"]} == expected
    assert any("Windows protects" in call.args[1] and "Refused" in call.args[1]
               for call in log.call_args_list)
    assert not any(call.args[0] == session.BAD for call in log.call_args_list)
    s.task_writes.clear()
    session.turn_off(s, journal, log, settle=0)
    assert s.task_state == before
    assert set(s.task_writes) == {(path, True) for path in expected}


def test_optional_task_can_be_selected_and_saved(monkeypatch, journal):
    monkeypatch.setattr(catalog, "TASKS", (catalog.TaskSpec(r"\Opt", "Optional", catalog.OPT),))
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    c = session.preset(catalog.REC, [])
    assert not c.paused_tasks
    c.paused_tasks.add(r"\Opt")
    c.preset = "custom"
    c = session.choices_from_config(c.to_config())
    assert c.paused_tasks == {r"\Opt"}
    s = TaskSystem()
    s.task_state = {r"\Opt": True}
    session.turn_on(s, c, journal, lambda *_: None, 999, 1)
    assert s.task_state[r"\Opt"] is False
    session.turn_off(s, journal, lambda *_: None, settle=0)
    assert s.task_state[r"\Opt"] is True


@pytest.mark.parametrize("level,spare,visible,closed", [
    (catalog.MAX, True, False, True), (catalog.REC, True, False, False),
    (catalog.MAX, False, False, False), (catalog.MAX, False, True, False),
    (catalog.MAX, True, True, False), (catalog.MAX, True, None, False),
])
def test_photos_only_closes_hidden_spare(monkeypatch, journal, level, spare, visible, closed):
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    s = TaskSystem()
    s.procs = [Proc(30, 1, "Photos.exe", 3672, 1, 130)]
    path = r"C:\Program Files\WindowsApps\Microsoft.Photos\Photos.exe"
    s.details = lambda _pid: (path, f'"{path}" ms-photos:' +
                             ("spareprocess-viewer" if spare else "viewer"), False)
    s.has_visible_window = lambda _pids: visible
    s.window_state = lambda _pids: "normal" if visible else "hidden"
    choices = session.preset(level, [])
    session.turn_on(s, choices, journal, lambda *_: None, 999, 1)
    assert (not s.procs) == closed
    session.turn_off(s, journal, lambda *_: None, settle=0)
    assert not s.launched


def test_task_com_stops_only_a_running_instance(monkeypatch):
    s = WinSystem.__new__(WinSystem)
    task = Mock(State=4)
    task.Stop.side_effect = lambda _flags: setattr(task, "State", 3)
    monkeypatch.setattr(s, "_task", lambda _path: task)
    assert s.task_running(r"\Task") is True
    assert s.stop_task(r"\Task") is None
    task.Stop.assert_called_once_with(0)
    task.Stop.side_effect = PermissionError("protected")
    assert "refused" in s.stop_task(r"\Task")


def test_stop_refusal_does_not_abort_or_lose_restore(monkeypatch, journal):
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    s = TaskSystem()
    path = r"\Microsoft\Windows\Defrag\ScheduledDefrag"
    s.running_tasks = {path}
    s.stop_task = lambda _path: "refused (protected)"
    log = Mock()
    session.turn_on(s, session.preset(catalog.MAX, []), journal, log, 999, 1)
    assert s.task_state[path] is False
    assert any("refused" in call.args[1] and call.args[0] == session.WARN
               for call in log.call_args_list)
    session.turn_off(s, journal, log, settle=0)
    assert s.task_state[path] is True


@pytest.mark.parametrize("path", [
    r"\Microsoft\Windows\PI\Secure-Boot-Update", r"\Microsoft\Windows\WaaSMedic\PerformRemediation",
    r"\Microsoft\XblGameSave\XblGameSaveTask", r"\AMDRyzenMasterSDKTask", r"\Bitdefender Update",
    r"\OneDrive Startup Task-S-1-5-21", r"\Microsoft\Windows\Chkdsk\SyspartRepair",
    r"\Microsoft\Windows\AppID\SmartScreenSpecific",
])
def test_excluded_tasks_are_never_catalogued(path):
    assert catalog.task_spec(path) is None


@pytest.mark.parametrize("path,level", [
    (r"\Git for Windows Updater", catalog.REC),
    (r"\WinClientTool_UnattendedMaintenance", catalog.REC),
    (r"\Microsoft\Windows\InstallService\ScanForUpdates", catalog.REC),
    (r"\Microsoft\Windows\InstallService\ScanForUpdatesAsUser", catalog.REC),
    (r"\Microsoft\Windows\InstallService\SmartRetry", catalog.REC),
    (r"\Microsoft\Windows\DiskCleanup\SilentCleanup", catalog.REC),
    (r"\Microsoft\Windows\Diagnosis\Scheduled", catalog.REC),
    (r"\Microsoft\Windows\Diagnosis\RecommendedTroubleshootingScanner", catalog.REC),
    (r"\Microsoft\Windows\DiskFootprint\Diagnostics", catalog.REC),
    (r"\Microsoft\Windows\Registry\RegIdleBackup", catalog.REC),
    (r"\Microsoft\Windows\Shell\IndexerAutomaticMaintenance", catalog.REC),
    (r"\Microsoft\Windows\Shell\ThemesSyncedImageDownload", catalog.REC),
    (r"\Microsoft\Windows\Speech\SpeechModelDownloadTask", catalog.REC),
    (r"\Microsoft\Windows\Maps\MapsToastTask", catalog.REC),
    (r"\Microsoft\Windows\PI\Sqm-Tasks", catalog.REC),
    (r"\Microsoft\Windows\MemoryDiagnostic\ProcessMemoryDiagnosticEvents", catalog.REC),
    (r"\SoftLanding\S-1-5-21\Tips", catalog.REC),
    (r"\Microsoft\Windows\Flighting\FeatureConfig\ReconcileFeatures", catalog.MAX),
    (r"\Microsoft\Windows\Flighting\OneSettings\RefreshCache", catalog.MAX),
    (r"\Microsoft\Windows\DiskFootprint\StorageSense", catalog.MAX),
    (r"\Microsoft\Windows\Chkdsk\ProactiveScan", catalog.MAX),
    (r"\Microsoft\Windows\Data Integrity Scan\Data Integrity Scan", catalog.MAX),
    (r"\Microsoft\Windows\StateRepository\MaintenanceTasks", catalog.MAX),
    (r"\Microsoft\Windows\UpdateOrchestrator\Schedule Scan", catalog.MAX),
    (r"\Microsoft\Windows\UpdateOrchestrator\Schedule Scan Static Task", catalog.MAX),
    (r"\Microsoft\Windows\UpdateOrchestrator\Schedule Work", catalog.MAX),
    (r"\Microsoft\Windows\UpdateOrchestrator\USO_UxBroker", catalog.MAX),
])
def test_added_task_levels(path, level):
    assert catalog.task_spec(path.upper()).level == level


def test_task_journal_precedes_disable_and_stop(monkeypatch, journal):
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    s = TaskSystem()
    path = r"\Microsoft\Windows\Defrag\ScheduledDefrag"
    s.task_state = {path: True}
    s.running_tasks = {path}
    disable = s.set_task_enabled

    def checked_disable(path, enabled):
        if not enabled:
            assert journal.actions[-1] == {"kind": "task", "path": path,
                                           "label": catalog.task_spec(path).label, "done": False}
        return disable(path, enabled)

    def checked_stop(path):
        assert journal.actions[-1]["done"] is True
        assert s.task_state[path] is False

    s.set_task_enabled = checked_disable
    s.stop_task = checked_stop
    session.turn_on(s, session.preset(catalog.MAX, []), journal, lambda *_: None, 999, 1)


def test_photos_mixed_spare_and_user_instance_is_left_open(monkeypatch, journal):
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)
    s = TaskSystem()
    s.procs = [Proc(30, 1, "Photos.exe", 3672, 1, 130), Proc(31, 1, "Photos.exe", 100, 1, 131)]
    path = r"C:\Program Files\WindowsApps\Microsoft.Photos\Photos.exe"
    s.details = lambda pid: (path, "spareprocess" if pid == 30 else "viewer", False)
    s.has_visible_window = lambda _pids: False
    session.turn_on(s, session.preset(catalog.MAX, []), journal, lambda *_: None, 999, 1)
    assert len(s.procs) == 2 and not s.close_batches
    assert not [e for e in journal.actions if e["kind"] == "app_close"]


def test_visible_window_probe_is_conservative(monkeypatch):
    import win32gui
    import win32process
    s = WinSystem.__new__(WinSystem)
    monkeypatch.setattr(win32gui, "EnumWindows", lambda cb, extra: cb(1, extra))
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda _h: (0, 30))
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda _h: True)
    assert s.has_visible_window([30]) is True
    assert s.has_visible_window([31]) is False
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda _h: False)
    assert s.has_visible_window([30]) is False

    def refused(_h):
        raise OSError("refused")

    monkeypatch.setattr(win32gui, "IsWindowVisible", refused)
    assert s.has_visible_window([30]) is None


def test_photos_condition_cannot_be_bypassed_as_another_apps_helper():
    procs = [Proc(29, 1, "deluge.exe", 100, 1, 129), Proc(30, 29, "Photos.exe", 3672, 1, 130)]
    groups = session.running_apps(procs, 1, 999, lambda pid: rf"C:\Apps\{pid}.exe")
    assert {g.exe for g in groups} == {"deluge.exe", "photos.exe"}


def test_legacy_maximum_config_includes_maximum_tasks():
    c = session.choices_from_config({"preset": catalog.MAX})
    assert c.paused_tasks == {t.pattern for t in catalog.TASKS if t.level in (catalog.REC, catalog.MAX)}
