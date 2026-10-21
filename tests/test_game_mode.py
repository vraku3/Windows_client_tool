"""Game Mode: ON then OFF leaves the machine exactly as it was.

Driven against a stateful fake system, so every decision in `session` is
exercised without touching this PC. `tools/game_mode_roundtrip.py` is the
real-machine counterpart.
"""
import copy
import json
import os
import threading
from unittest.mock import Mock

import pytest

from modules.game_mode.engine import advice, catalog, session
from modules.game_mode.engine.journal import Journal
from modules.game_mode.engine.system import BELOW_NORMAL, NORMAL, Proc, ServiceState, \
    WinSystem, parse_plans, split_command_line

ULTIMATE = "a4b7a961-79a6-4b48-b587-e9d886c99568"
X3D = "AMD Ryzen 9 9950X3D 16-Core Processor"


class FakeSystem:
    def __init__(self, cpu=X3D, admin=True):
        self.cpu = cpu
        self.admin = admin
        self.plan = ULTIMATE
        self.plan_names = {session.BALANCED: "Balanced", ULTIMATE: "Ultimate Performance",
                           session.HIGH_PERFORMANCE: "High performance"}
        self.svc = {"WSearch": ["Running", "Automatic"], "BITS": ["Running", "Automatic"],
                    "Spooler": ["Stopped", "Disabled"], "TrkWks": ["Running", "Automatic"],
                    catalog.VCACHE_SERVICE: ["Running", "Automatic"]}
        self.reg = {("HKCU", session.GAMEBAR_KEY, "AutoGameModeEnabled"): (1, 4),
                    ("HKCU", session.THEMES_KEY, "EnableTransparency"): (1, 4)}
        self.broadcasts = []
        self.task_state = {"\\MicrosoftEdgeUpdateTaskMachineCore{X}": True,
                           "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag": True,
                           "\\Microsoft\\Windows\\Maintenance\\WinSAT": False,
                           "\\Microsoft\\Windows\\Feedback\\Siuf\\DmClient": True,
                           "\\MyOwnTask": True}
        self.protected = {"\\Microsoft\\Windows\\Feedback\\Siuf\\DmClient"}
        self.anim = True
        self.fullscreen = set()
        self.wifi = False
        self.explorer_restarts = 0
        self.close_batches = []
        self.procs = [Proc(10, 1, "OneDrive.exe", 3000, 1, 100), Proc(11, 1, "chrome.exe", 5000, 1, 101),
                      Proc(12, 11, "chrome.exe", 900, 1, 102), Proc(13, 1, "explorer.exe", 9000, 1, 103),
                      Proc(14, 1, "deluge.exe", 1900, 1, 104), Proc(20, 1, "svchost.exe", 800, 0, 105)]
        self.prio = {11: NORMAL, 12: NORMAL}
        self.paths = {10: r"C:\Program Files\Microsoft OneDrive\OneDrive.exe",
                      11: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      12: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      13: r"C:\Windows\explorer.exe", 14: r"C:\Program Files\Deluge\deluge.exe"}
        self.launched = []
        self.fail_close = set()
        self.next_pid = 100

    # identity / plans
    def is_admin(self): return self.admin
    def cpu_name(self): return self.cpu
    def plans(self): return dict(self.plan_names)
    def active_plan(self): return self.plan

    def set_plan(self, guid):
        self.plan = guid

    # services
    def service(self, name):
        if name not in self.svc:
            return None
        status, start = self.svc[name]
        return ServiceState(status == "Running", start)

    def running_dependents(self, name): return []

    def stop_service(self, name):
        self.svc[name][0] = "Stopped"

    def start_service(self, name):
        if self.svc[name][1] == "Disabled":
            return "disabled"
        self.svc[name][0] = "Running"

    # processes
    def processes(self): return list(self.procs)

    def details(self, pid):
        path = self.paths.get(pid)
        return path, (f'"{path}" /background' if path else None), False

    def aumid(self, pid): return None
    def priority(self, pid): return self.prio.get(pid)

    def set_priority(self, pid, value):
        self.prio[pid] = value

    def close(self, pids, exe_path):
        if exe_path and exe_path.lower().endswith(tuple(self.fail_close)):
            return "refused"
        self.procs = [p for p in self.procs if (p.pid, p.create_time) not in pids]

    def fullscreen_pids(self): return set(self.fullscreen)

    def wifi_connected(self): return self.wifi

    def restart_explorer(self, session):
        self.explorer_restarts += 1
        for p in list(self.procs):
            if p.name == 'explorer.exe':
                self.procs.remove(p)
                self.procs.append(Proc(p.pid + 1000, 1, 'explorer.exe', 3000, 1, p.create_time + 1))

    def close_many(self, jobs, grace=8.0):
        self.close_batches.append(len(jobs))
        return [self.close(pids, path) for pids, path in jobs]

    def alive(self, pid, ct): return any(p.pid == pid and p.create_time == ct for p in self.procs)

    def launch(self, program, args="", cwd=""):
        self.launched.append((program, args))
        self.next_pid += 1
        name = program.rsplit("\\", 1)[-1]
        self.procs.append(Proc(self.next_pid, 1, name, 100, 1, self.next_pid))
        self.paths[self.next_pid] = program

    def launch_aumid(self, aumid): return self.launch(aumid)

    # registry
    def reg_get(self, path, name, hive="HKCU"):
        if (hive, path, name) in self.reg:
            value, kind = self.reg[(hive, path, name)]
            return True, value, kind
        return False, None, 0

    def reg_set(self, path, name, value, kind, hive="HKCU"):
        self.reg[(hive, path, name)] = (value, kind)

    def reg_delete(self, path, name, hive="HKCU"):
        self.reg.pop((hive, path, name), None)

    def broadcast_setting_change(self, area):
        self.broadcasts.append(area)

    # services by pattern, tasks, animations
    def service_names(self, pattern):
        import fnmatch
        return sorted(n for n in self.svc if fnmatch.fnmatch(n.lower(), pattern.lower()))

    def tasks(self):
        return list(self.task_state.items())

    def task_enabled(self, path):
        return self.task_state.get(path)

    def task_running(self, path):
        return False

    def set_task_enabled(self, path, enabled):
        if path in self.protected:
            return "access denied"
        self.task_state[path] = enabled

    def animations(self):
        return self.anim

    def set_animations(self, on):
        self.anim = on

    def state(self):
        names = sorted(p.name.lower() for p in self.procs)
        return (self.plan, copy.deepcopy(self.svc), dict(self.reg), names,
                {k: v for k, v in self.prio.items() if self.alive(k, k + 90)},
                dict(self.task_state), self.anim)


def _quiet(_level, _text):
    pass


@pytest.fixture
def journal(tmp_path):
    return Journal(str(tmp_path))


def _choices(**kw):
    c = session.Choices(services={"WSearch", "BITS", "Spooler", "TrkWks"},
                        close={"onedrive.exe", "deluge.exe"})
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_on_then_off_is_exactly_the_starting_state(journal):
    sys_ = FakeSystem()
    before = sys_.state()
    data = session.turn_on(sys_, _choices(), journal, _quiet, own_pid=999, session=1)
    assert sys_.plan == session.BALANCED                        # X3D -> Balanced
    assert sys_.svc["WSearch"][0] == "Stopped" and sys_.svc["BITS"][0] == "Stopped"
    assert sys_.svc["Spooler"] == ["Stopped", "Disabled"]       # already stopped: untouched
    assert {p.name for p in sys_.procs} == {"chrome.exe", "explorer.exe", "svchost.exe"}
    assert sys_.prio == {11: BELOW_NORMAL, 12: BELOW_NORMAL}
    assert sys_.reg[("HKCU", session.GAMEBAR_KEY, "AllowAutoGameMode")] == (1, 4)
    assert sys_.reg[("HKLM", session.MAINTENANCE_KEY, "MaintenanceDisabled")] == (1, 4)
    assert sys_.task_state["\\Microsoft\\Windows\\Defrag\\ScheduledDefrag"] is False
    assert sys_.task_state["\\MyOwnTask"] is True           # not in the catalog: untouched
    assert data["handles_before"] > data["handles_after"]
    report = session.turn_off(FakeSystemProxy(sys_), journal, _quiet, settle=0.5)
    assert not [r for r in report if r["level"] == session.BAD], report
    assert sys_.plan == ULTIMATE
    assert sys_.svc == before[1]
    assert ("HKCU", session.GAMEBAR_KEY, "AllowAutoGameMode") not in sys_.reg   # was absent
    assert ("HKLM", session.MAINTENANCE_KEY, "MaintenanceDisabled") not in sys_.reg
    assert sys_.task_state == before[5]
    assert sys_.state()[:3] == before[:3]
    assert sys_.prio == {11: NORMAL, 12: NORMAL}
    assert sorted(n for n, _a in sys_.launched) == [r"C:\Program Files\Deluge\deluge.exe",
                                                   r"C:\Program Files\Microsoft OneDrive\OneDrive.exe"]
    assert ("C:\\Program Files\\Microsoft OneDrive\\OneDrive.exe", "/background") in sys_.launched
    assert not journal.active and not Journal(journal.folder).load()


class FakeSystemProxy:
    """The same fake, so OFF sees what ON did (a new process would read it again)."""
    def __init__(self, inner):
        self.__dict__["_inner"] = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_off_works_from_the_journal_alone_after_a_crash(journal, tmp_path):
    sys_ = FakeSystem()
    session.turn_on(sys_, _choices(), journal, _quiet, own_pid=999, session=1)
    fresh = Journal(str(tmp_path))            # a new app start: memory gone, file remains
    assert fresh.load() and fresh.active
    report = session.turn_off(sys_, fresh, _quiet, settle=0.5)
    assert not [r for r in report if r["level"] == session.BAD]
    assert sys_.plan == ULTIMATE and sys_.svc["WSearch"][0] == "Running"


def test_a_change_journaled_but_interrupted_is_still_undone(journal):
    sys_ = FakeSystem()
    journal.begin()
    journal.record({"kind": "plan", "before": ULTIMATE, "before_name": "Ultimate", "after": session.BALANCED})
    sys_.plan = session.BALANCED              # applied, then the app died before mark_done
    journal.record({"kind": "service_stop", "name": "WSearch", "label": "Search", "start_type": "Automatic"})
    sys_.svc["WSearch"][0] = "Stopped"
    session.turn_off(sys_, journal, _quiet, settle=0.1)
    assert sys_.plan == ULTIMATE and sys_.svc["WSearch"][0] == "Running"


def test_nothing_is_reopened_twice_or_restarted_when_already_back(journal):
    sys_ = FakeSystem()
    session.turn_on(sys_, _choices(), journal, _quiet, own_pid=999, session=1)
    sys_.svc["WSearch"][0] = "Running"        # Windows restarted it (or a reboot)
    sys_.launch(r"C:\Program Files\Microsoft OneDrive\OneDrive.exe")   # the user opened it
    sys_.launched.clear()
    report = session.turn_off(sys_, journal, _quiet, settle=0.5)
    assert [n for n, _ in sys_.launched] == [r"C:\Program Files\Deluge\deluge.exe"]
    assert any("already running again" in r["text"] for r in report)


def test_a_priority_changed_since_is_left_alone(journal):
    sys_ = FakeSystem()
    session.turn_on(sys_, _choices(), journal, _quiet, own_pid=999, session=1)
    sys_.prio[11] = 0x80                      # someone set chrome to High meanwhile
    session.turn_off(sys_, journal, _quiet, settle=0.5)
    assert sys_.prio[11] == 0x80 and sys_.prio[12] == NORMAL


def test_unelevated_leaves_services_alone_and_says_so(journal):
    sys_ = FakeSystem(admin=False)
    lines = []
    session.turn_on(sys_, _choices(), journal, lambda lvl, t: lines.append((lvl, t)), 999, 1)
    assert sys_.svc["WSearch"][0] == "Running"
    assert any("needs administrator" in t for _l, t in lines)


def test_a_refused_close_is_reported_and_not_reopened(journal):
    sys_ = FakeSystem()
    sys_.fail_close = {"deluge.exe"}
    lines = []
    session.turn_on(sys_, _choices(), journal, lambda lvl, t: lines.append((lvl, t)), 999, 1)
    assert any(lvl == session.BAD and "Deluge" in t for lvl, t in lines)
    session.turn_off(sys_, journal, _quiet, settle=0.3)
    assert r"C:\Program Files\Deluge\deluge.exe" not in [n for n, _ in sys_.launched]


def test_enforce_stops_again_every_stopped_service_but_gives_up_after_three(journal):
    sys_ = FakeSystem()
    session.turn_on(sys_, _choices(), journal, _quiet, own_pid=999, session=1)
    lines = []
    for _round in range(5):
        sys_.svc["WSearch"][0] = sys_.svc["TrkWks"][0] = "Running"
        session.enforce(sys_, journal, lambda lvl, t: lines.append((lvl, t)))
    assert sum("TrkWks" in t or "Distributed Link" in t for _l, t in lines if "stopped it again" in t) == 3
    assert any("left running from now on" in t for _l, t in lines)
    assert sys_.svc["WSearch"][0] == "Running"          # given up after three
    session.turn_off(sys_, journal, _quiet, settle=0.1)
    assert sys_.svc["WSearch"][0] == "Running" and sys_.svc["TrkWks"][0] == "Running"


def test_plan_choice_follows_the_cpu():
    plans = {session.BALANCED: "Balanced", ULTIMATE: "Ultimate Performance",
             session.HIGH_PERFORMANCE: "High performance"}
    assert session.recommended_plan(X3D, plans, True)[0] == session.BALANCED
    assert session.recommended_plan("AMD Ryzen 7 9800X3D 8-Core Processor", plans, True)[0] == ULTIMATE
    assert session.recommended_plan("Intel(R) Core(TM) i9-14900K", {session.BALANCED: "Balanced",
                                    session.HIGH_PERFORMANCE: "High"}, False)[0] == session.HIGH_PERFORMANCE
    assert session.recommended_plan(X3D, plans, False)[0] == ULTIMATE   # no optimizer driver


def test_windows_own_programs_and_launchers_are_never_offered():
    sys_ = FakeSystem()
    sys_.procs.append(Proc(30, 1, "Battle.net.exe", 2000, 1, 120))
    sys_.paths[30] = r"C:\Program Files (x86)\Battle.net\Battle.net.exe"
    groups = session.running_apps(sys_.procs, 1, 999, lambda pid: sys_.paths.get(pid))
    names = {g.exe for g in groups}
    assert names == {"onedrive.exe", "chrome.exe", "deluge.exe"}
    chrome = next(g for g in groups if g.exe == "chrome.exe")
    assert [p.pid for p in chrome.roots] == [11] and chrome.handles == 5900


def test_choices_round_trip_and_reject_junk():
    c = session.Choices(plan=False, services={"WSearch"}, close={"deluge.exe"})
    assert session.choices_from_config(c.to_config()) == c
    junk = session.choices_from_config({"plan": "yes", "services": [1, "BITS"], "close": "x"})
    assert junk.plan is True and junk.services == {"BITS"}


def test_parsers():
    text = ("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n"
            "Power Scheme GUID: a4b7a961-79a6-4b48-b587-e9d886c99568  (Ultimate Performance) *")
    assert parse_plans(text) == {session.BALANCED: "Balanced", ULTIMATE: "Ultimate Performance"}
    assert split_command_line('"C:\\A B\\x.exe" /background --flag') == ("C:\\A B\\x.exe",
                                                                        "/background --flag")
    assert split_command_line("C:\\x.exe -a") == ("C:\\x.exe", "-a")


def test_advice_states_what_it_read():
    f = advice.Facts(cpu=X3D, vcache=True, vcache_installed=True, plan_guid=ULTIMATE,
                     plan_name="Ultimate Performance", gamebar_version="", auto_game_mode=1,
                     game_dvr=0, app_capture=0, hags=2, hvci_running=None,
                     displays=[advice.Display("RX 7900 XTX", 2560, 1440, 60.0, 144.0)],
                     torrents=["deluge.exe"])
    items = advice.findings(f)
    titles = " | ".join(i.title for i in items)
    assert "not Balanced" in titles and "Game Bar is not installed" in titles
    assert "runs at 60 Hz -- it can do 144 Hz" in titles
    assert "torrent" in titles
    assert any(i.level == advice.UNKNOWN and "Memory integrity" in i.title for i in items)
    assert items[0].level == advice.ACT                 # actions first


def test_security_software_is_never_in_either_list():
    names = {s.name.lower() for s in catalog.SERVICES} | set(catalog.APP_BY_EXE)
    for forbidden in ("windefend", "mpssvc", "wscsvc", "sense", "bdservicehost.exe", "msmpeng.exe",
                      "vsserv", "bdagent.exe"):
        assert forbidden not in names
    assert catalog.never_close("bdservicehost.exe") and catalog.never_close("battle.net.exe")
    for spec in catalog.SERVICES:
        assert not any(av in spec.name.lower() for av in ("windefend", "mpssvc", "wscsvc", "bdredline",
                                                           "vsserv", "bdappsrv", "bdauxsrv"))


def test_maximum_leaves_unknown_programs_and_restores_visuals(journal):
    sys_ = FakeSystem()
    sys_.procs.append(Proc(40, 1, "unknown-tool.exe", 4900, 1, 130))
    sys_.paths[40] = r"C:\Program Files\Unknown\unknown-tool.exe"
    groups = session.running_apps(sys_.procs, 1, 999, lambda pid: sys_.paths.get(pid))
    maximum = session.preset(catalog.MAX, groups)
    recommended = session.preset(catalog.REC, groups)
    assert "unknown-tool.exe" not in maximum.close and "unknown-tool.exe" not in recommended.close
    assert "chrome.exe" in maximum.close and "chrome.exe" not in recommended.close
    assert maximum.visuals and not recommended.visuals
    assert recommended.services < maximum.services
    before = sys_.state()
    session.turn_on(sys_, maximum, journal, _quiet, own_pid=999, session=1)
    assert sys_.anim is False and sys_.reg[("HKCU", session.THEMES_KEY, "EnableTransparency")][0] == 0
    assert "ImmersiveColorSet" in sys_.broadcasts
    chrome_launch = None
    session.turn_off(sys_, journal, _quiet, settle=0.5)
    chrome_launch = [a for n, a in sys_.launched if n.endswith("chrome.exe")]
    assert chrome_launch and "--restore-last-session" in chrome_launch[0]
    assert sys_.anim is True and sys_.reg[("HKCU", session.THEMES_KEY, "EnableTransparency")][0] == 1
    assert sys_.state()[:3] == before[:3] and sys_.state()[5:] == before[5:]


def test_a_protected_task_is_reported_and_never_journaled(journal):
    sys_ = FakeSystem()
    lines = []
    session.turn_on(sys_, _choices(), journal, lambda lvl, t: lines.append((lvl, t)), 999, 1)
    assert sys_.task_state["\\Microsoft\\Windows\\Feedback\\Siuf\\DmClient"] is True
    assert any("Feedback surveys" in t for _l, t in lines)
    assert not any(e.get("path", "").endswith("DmClient") and e.get("done") for e in journal.actions)


def test_peripherals_office_terminals_and_agents_are_never_closed():
    for exe in ("OUTLOOK.EXE", "lghub_agent.exe", "SteelSeriesGG.exe", "RazerAppEngine.exe",
                "claude.exe", "WindowsTerminal.exe", "bdservicehost.exe", "Battle.net.exe",
                "WINWORD.EXE", "pwsh.exe"):
        assert catalog.never_close(exe), exe
    for exe in ("chrome.exe", "onedrive.exe", "AppControl.exe"):
        assert not catalog.never_close(exe), exe


def test_freed_estimate_counts_the_web_views_of_closed_apps():
    procs = [Proc(10, 1, "WhatsApp.Root.exe", 4000, 1, 1), Proc(11, 10, "msedgewebview2.exe", 2000, 1, 2),
             Proc(12, 11, "msedgewebview2.exe", 900, 1, 3), Proc(20, 1, "Widgets.exe", 100, 1, 4),
             Proc(21, 1, "msedgewebview2.exe", 700, 1, 5)]
    groups = session.running_apps(procs, 1, 999, lambda pid: "C:\\Program Files\\x\\" + str(pid))
    assert session.freed_estimate(procs, groups, {"whatsapp.root.exe"}) == 4000 + 2000 + 900
    assert session.freed_estimate(procs, groups, set()) == 0


def test_service_patterns_resolve_to_installed_instances():
    assert catalog.service_spec("OneSyncSvc_103c87").label == "Sync Host"
    assert catalog.service_spec("GoogleUpdaterService157.0.8089.0").label == "Google Updater"
    assert catalog.service_spec("WinDefend") is None


def _groups(procs, paths, protect=None):
    return session.running_apps(procs, 1, 999, lambda pid: paths.get(pid), protect)


def test_a_running_game_is_never_closed_by_maximum():
    procs = [Proc(1, 0, "explorer.exe", 9000, 1, 1),
             Proc(50, 1, "steam.exe", 1000, 1, 10), Proc(51, 50, "eldenring.exe", 4000, 1, 20),
             Proc(60, 1, "Battle.net.exe", 2000, 1, 11), Proc(61, 60, "Wow.exe", 6000, 1, 21),
             Proc(70, 1, "standalone.exe", 3000, 1, 12), Proc(80, 1, "fullscreen.exe", 3000, 1, 13),
             Proc(90, 1, "AppControl.exe", 500, 1, 14)]
    paths = {51: r"D:\SteamLibrary\steamapps\common\ELDEN RING\Game\eldenring.exe",
             61: r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe",
             70: r"E:\Games\Standalone\standalone.exe",
             80: r"C:\Program Files\Something\fullscreen.exe",
             90: r"C:\Program Files\AppControl\ui\AppControl.exe"}
    groups = _groups(procs, paths, protect={80})
    names = {g.exe for g in groups}
    assert names == {"appcontrol.exe"}            # launcher child, game folder, fullscreen: kept
    assert "eldenring.exe" not in session.preset(catalog.MAX, groups).close


def test_a_reused_pid_is_not_taken_for_a_launcher_parent():
    # pid 50 is Steam NOW, but it started after the process whose ppid says 50
    procs = [Proc(50, 1, "steam.exe", 1000, 1, 500), Proc(51, 50, "oldtool.exe", 400, 1, 100)]
    groups = _groups(procs, {51: r"C:\Program Files\Tool\oldtool.exe"})
    assert {g.exe for g in groups} == {"oldtool.exe"}


def test_helpers_are_folded_into_the_app_that_started_them():
    procs = [Proc(10, 1, "Code.exe", 7000, 1, 1), Proc(11, 10, "Code.exe", 500, 1, 2),
             Proc(12, 11, "pet.exe", 75, 1, 3), Proc(13, 10, "copilot-runtime.exe", 284, 1, 4),
             Proc(20, 1, "deluge.exe", 1900, 1, 5)]
    paths = {10: r"C:\Users\u\AppData\Local\Programs\Microsoft VS Code\Code.exe",
             12: r"C:\Users\u\.vscode\extensions\x\pet.exe",
             13: r"C:\Users\u\AppData\Local\Programs\Microsoft VS Code\x\copilot-runtime.exe",
             20: r"C:\Program Files\Deluge\deluge.exe"}
    groups = _groups(procs, paths)
    assert {g.exe for g in groups} == {"code.exe", "deluge.exe"}
    code = next(g for g in groups if g.exe == "code.exe")
    assert code.handles == 7000 + 500 + 75 + 284


def test_discord_is_closed_by_maximum_and_reopened():
    spec = catalog.spec_for("Discord.exe")
    assert spec.level == catalog.MAX and spec.reopen
    for exe in ("DefenderSessionHelper.exe", "GameInputRedistService.exe", "bash.exe",
                "gameoverlayui64.exe", "parsecd.exe"):
        assert catalog.never_close(exe), exe


def test_wlan_is_stopped_only_without_wifi(journal):
    for wifi, expect in ((False, "Stopped"), (True, "Running"), (None, "Running")):
        sys_ = FakeSystem()
        sys_.svc["WlanSvc"] = ["Running", "Automatic"]
        sys_.wifi = wifi
        j = Journal(journal.folder + str(wifi))
        session.turn_on(sys_, _choices(services={"WlanSvc"}), j, _quiet, own_pid=999, session=1)
        assert sys_.svc["WlanSvc"][0] == expect, wifi
        session.turn_off(sys_, j, _quiet, settle=0.1)
        assert sys_.svc["WlanSvc"][0] == "Running"


def test_explorer_restart_requires_explicit_opt_in_and_off_needs_nothing(journal):
    sys_ = FakeSystem()
    groups = session.running_apps(sys_.procs, 1, 999, lambda pid: sys_.paths.get(pid))
    maximum = session.preset(catalog.MAX, groups)
    assert not maximum.explorer and not session.preset(catalog.REC, groups).explorer
    maximum.explorer = True
    lines = []
    session.turn_on(sys_, maximum, journal, lambda lvl, t: lines.append((lvl, t)), 999, 1)
    assert sys_.explorer_restarts == 1
    assert "Explorer restarted fresh" in lines[-1][1] or any("Explorer restarted" in t for _l, t in lines)
    report = session.turn_off(sys_, journal, _quiet, settle=0.5)
    assert any("Explorer was restarted fresh" in r["text"] for r in report)
    assert sys_.explorer_restarts == 1


def test_searchhost_is_not_closed_by_maximum_it_comes_back_in_a_second():
    assert catalog.spec_for("SearchHost.exe").level == catalog.OPT


def test_programs_are_closed_together_in_one_batch(journal):
    sys_ = FakeSystem()
    session.turn_on(sys_, _choices(), journal, _quiet, own_pid=999, session=1)
    assert sys_.close_batches == [2]                     # OneDrive and Deluge, one batch


def test_a_preset_follows_the_catalog_until_ticked_by_hand():
    groups = []
    c = session.preset(catalog.MAX, groups)
    assert c.preset == catalog.MAX
    back = session.choices_from_config(c.to_config())
    assert back.preset == catalog.MAX and back == c
    old = session.choices_from_config({"services": ["WSearch"], "close": []})   # saved before presets
    assert old.preset == "custom"


def test_log_lines_before_the_pane_is_built_are_kept_not_crashed(qapp, tmp_path):
    """Game Mode left ON starts the enforce timer in on_start; its first line
    used to hit `self._log` before create_widget made it (AttributeError)."""
    from modules.game_mode.game_mode_module import GameModeModule
    m = GameModeModule()
    m._journal = Journal(str(tmp_path))
    m._append(f"{session.INFO}\tUpdate Orchestrator was started again by Windows -- stopped it again")
    widget = m.create_widget()
    try:
        assert "Update Orchestrator was started again" in m._log.toPlainText()
        assert m._pending_log == []
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("throws", [False, True])
def test_failed_restore_keeps_only_unresolved_entries_for_retry(journal, monkeypatch, throws):
    sys_ = FakeSystem()
    journal.begin()
    entry = journal.record({"kind": "service_stop", "name": "WSearch", "label": "Search"})
    journal.record({"kind": "registry", "hive": "HKCU", "path": session.GAMEBAR_KEY,
                    "name": "AllowAutoGameMode", "existed": False, "value": None, "type": 0})
    sys_.svc["WSearch"][0] = "Stopped"
    def refused(_name):
        if throws:
            raise OSError("access denied")
        return "access denied"
    monkeypatch.setattr(sys_, "start_service", refused)
    report = session.turn_off(sys_, journal, _quiet, settle=0)
    fresh = Journal(journal.folder)
    assert fresh.load() and fresh.actions == [entry]
    assert any(r["level"] == session.BAD and "Recovery incomplete: 1" in r["text"] for r in report)
    monkeypatch.setattr(sys_, "start_service", lambda name: sys_.svc[name].__setitem__(0, "Running"))
    session.turn_off(sys_, fresh, _quiet, settle=0)
    assert not fresh.active and not os.path.exists(fresh.path)


def test_corrupt_journal_is_preserved(journal):
    os.makedirs(journal.folder, exist_ok=True)
    with open(journal.path, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    assert journal.load()
    report = session.turn_off(FakeSystem(), journal, _quiet, settle=0)
    with open(journal.path, encoding="utf-8") as fh:
        assert fh.read() == "{broken"
    assert journal.active and any(r["level"] == session.BAD for r in report)


@pytest.mark.parametrize("entry", [
    {"kind": "registry", "hive": "HKLM", "path": r"SOFTWARE\Tampered", "name": "Evil",
     "existed": True, "value": 1, "type": 4},
    {"kind": "service_stop", "name": "WinDefend", "label": "Tampered"},
    {"kind": "service_start", "name": "UnknownService"},
    {"kind": "task", "path": r"\MyOwnTask"},
    {"kind": "plan", "before": "not-a-guid"},
    {"kind": "priority", "before": 12345},
    {"kind": "app_close", "path": "relative.exe", "aumid": None},
    {"kind": "app_close", "path": "", "aumid": "shell:malicious"},
])
def test_tampered_journal_entries_are_rejected_without_writes(journal, entry):
    journal.begin()
    journal.record(entry)
    sys_ = FakeSystem()
    writes = ["reg_set", "reg_delete", "start_service", "stop_service", "set_task_enabled",
              "set_plan", "set_priority", "launch", "launch_aumid"]
    for name in writes:
        setattr(sys_, name, Mock(side_effect=AssertionError("must not write")))
    report = session.turn_off(sys_, journal, _quiet, settle=0)
    assert any(r["level"] == session.BAD and "Rejected journal entry" in r["text"]
               and "not something Game Mode changes" in r["text"] for r in report)
    for name in writes:
        getattr(sys_, name).assert_not_called()
    assert not journal.active


def test_enforce_returns_immediately_when_operation_lock_is_held(journal):
    journal.begin()
    with session.OPERATION_LOCK:
        assert session.enforce(FakeSystem(), journal, _quiet) == 0


def test_enforce_rejects_unknown_service(journal):
    journal.begin()
    entry = journal.record({"kind": "service_stop", "name": "WinDefend"})
    journal.mark_done(entry)
    sys_ = FakeSystem()
    sys_.svc["WinDefend"] = ["Running", "Automatic"]
    assert session.enforce(sys_, journal, _quiet) == 0
    assert sys_.svc["WinDefend"][0] == "Running"


def test_close_many_skips_pid_recycled_before_close_or_kill(monkeypatch):
    import psutil
    sys_ = WinSystem()
    original = Proc(123, 1, "tool.exe", 1, 1, 100)
    recycled = Proc(123, 1, "other.exe", 1, 1, 200)
    monkeypatch.setattr(sys_, "processes", lambda: [recycled])
    post = Mock()
    monkeypatch.setattr(sys_, "_post_close", post)
    monkeypatch.setattr(sys_, "_wait_gone", lambda *_: True)
    process = Mock()
    monkeypatch.setattr(psutil, "Process", process)
    assert sys_.close_many([([(123, 100)], None)], grace=0) == [None]
    assert not any(call.args[0] for call in post.call_args_list)
    process.assert_not_called()
    monkeypatch.setattr(sys_, "processes", lambda: [original])
    monkeypatch.setattr(sys_, "_post_close", lambda *_: setattr(sys_, "processes", lambda: [recycled]))
    assert sys_.close_many([([(123, 100)], None)], grace=0) == [None]
    process.assert_not_called()


def test_lower_skips_recycled_pid(journal, monkeypatch):
    sys_ = FakeSystem()
    groups = _groups(sys_.procs, sys_.paths)
    sys_.procs = [Proc(p.pid, p.ppid, p.name, p.handles, p.session, p.create_time + 1)
                  for p in sys_.procs]
    journal.begin()
    changed = Mock()
    monkeypatch.setattr(sys_, "set_priority", changed)
    session._On(sys_, journal, _quiet).lower(groups, set())
    changed.assert_not_called()


def test_antivirus_vendors_share_never_close_and_advice_patterns():
    for exe in ("avp.exe", "egui.exe", "ekrn.exe", "defendersessionhelper.exe", "smartscreen.exe",
                "avastsvc.exe", "mcshield.exe"):
        assert catalog.never_close(exe), exe
    assert not catalog.never_close("ns-tool.exe")
    f = advice.Facts()
    advice._floor(f, [Proc(1, 0, "avp.exe", 12, 1, 1), Proc(2, 0, "ekrn.exe", 13, 0, 2)])
    assert f.antivirus_handles == 25


@pytest.mark.parametrize("cpu", [X3D, "Intel Core i7"])
def test_unreadable_vcache_is_unknown(cpu):
    items = advice.findings(advice.Facts(cpu=cpu, vcache_installed=True, vcache=None))
    assert any(i.level == advice.UNKNOWN and
               i.title == "AMD 3D V-Cache optimizer: state could not be read" for i in items)
    assert not any(i.level == advice.GOOD and "optimizer" in i.title for i in items)


def test_advice_hags_and_hvci_do_not_claim_driver_state_or_performance():
    items = advice.findings(advice.Facts(hags=2, hvci_running=False))
    text = " ".join(i.title + " " + i.detail + " " + i.fix for i in items)
    assert "requested in Windows (HwSchMode = 2)" in text
    assert "depends on the driver" in text
    assert "required for frame generation" not in text
    assert "No virtualization-based overhead" not in text


def test_old_session_warns_once_each_enforce_run(journal):
    import time
    journal.begin()
    journal.data["started"] = time.time() - 7 * 3600 - 12 * 60
    lines = []
    session.enforce(FakeSystem(), journal, lambda lvl, text: lines.append((lvl, text)))
    assert len([t for lvl, t in lines if lvl == session.WARN and "still paused" in t]) == 1


@pytest.mark.parametrize("trusted", [False, True])
def test_onedrive_shutdown_uses_only_allowed_paths_and_unelevated_shell(monkeypatch, tmp_path, trusted):
    from modules.game_mode.engine import shell_launch
    from modules.game_mode.engine import system as system_module
    base = tmp_path / "Programs"
    monkeypatch.setenv("ProgramFiles", str(base))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    path = str(base / ("Microsoft OneDrive" if trusted else "Other") / "OneDrive.exe")
    sys_ = WinSystem()
    monkeypatch.setattr(sys_, "processes", lambda: [Proc(123, 1, "OneDrive.exe", 1, 1, 100)])
    monkeypatch.setattr(sys_, "_post_close", lambda *_: setattr(sys_, "processes", lambda: []))
    monkeypatch.setattr(sys_, "_wait_gone", lambda *_: True)
    launch = Mock(return_value=None)
    run = Mock(side_effect=AssertionError("must not launch elevated"))
    monkeypatch.setattr(shell_launch, "launch", launch)
    monkeypatch.setattr(system_module, "run", run)
    assert sys_.close_many([([(123, 100)], path)], grace=0) == [None]
    if trusted:
        launch.assert_called_once_with(path, "/shutdown")
    else:
        launch.assert_not_called()
    run.assert_not_called()


def test_onedrive_path_allowlist_resolves_paths_and_honours_boundaries(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Programs"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert WinSystem._trusted_onedrive(str(tmp_path / "programs" / "MICROSOFT ONEDRIVE" / "OneDrive.exe"))
    assert WinSystem._trusted_onedrive(str(tmp_path / "Local" / "Microsoft" / "OneDrive" / "OneDrive.exe"))
    assert not WinSystem._trusted_onedrive(str(tmp_path / "Programs" / "Microsoft OneDrive Evil" / "OneDrive.exe"))
    assert not WinSystem._trusted_onedrive(str(tmp_path / "Programs" / "Microsoft OneDrive" / ".." / "OneDrive.exe"))


def test_post_close_rechecks_identity_at_each_window(monkeypatch):
    import win32gui
    import win32process
    sys_ = WinSystem()
    monkeypatch.setattr(sys_, "processes", lambda: [Proc(123, 1, "other.exe", 1, 1, 200)])
    monkeypatch.setattr(win32gui, "EnumWindows", lambda callback, arg: callback(77, arg))
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda _hwnd: (1, 123))
    post = Mock()
    monkeypatch.setattr(win32gui, "PostMessage", post)
    sys_._post_close([(123, 100)])
    post.assert_not_called()


def test_off_waits_for_enforce_finished_signal(qapp, journal, monkeypatch):
    from modules.game_mode.game_mode_module import GameModeModule
    m = GameModeModule()
    m._journal = journal
    journal.begin()
    m._enforce_worker = object()
    widget = m.create_widget()
    run = Mock()
    monkeypatch.setattr(m, "_run", run)
    try:
        m._turn_off()
        assert m._busy and m._off_pending
        run.assert_not_called()
        m._enforce_finished()
        assert m._enforce_worker is None and not m._off_pending
        run.assert_called_once()
    finally:
        widget.deleteLater()


def test_failed_off_ui_stays_on_with_recovery_banner(qapp, journal, monkeypatch):
    from modules.game_mode import game_mode_module as ui
    m = ui.GameModeModule()
    m._journal = journal
    journal.begin()
    widget = m.create_widget()
    monkeypatch.setattr(ui, "_keep_awake", lambda _on: None)
    monkeypatch.setattr(m, "_refresh_all", lambda: None)
    banner = Mock()
    monkeypatch.setattr(m, "_show_error", banner)
    try:
        text = "Recovery incomplete: 1 change(s) could not be put back -- Game Mode stays ON"
        m._off_done([{"level": session.BAD, "text": text}])
        assert "Game Mode is ON for" in m._state.text()
        assert m._off_btn.isEnabled() and not m._on_btn.isEnabled()
        banner.assert_called_with(text)
    finally:
        widget.deleteLater()


def test_confirmation_warns_of_lost_work_and_defaults_to_cancel(qapp, monkeypatch):
    from modules.game_mode.game_mode_module import GameModeModule, QMessageBox
    m = GameModeModule()
    question = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", question)
    assert not m._confirm_on()
    text = question.call_args.args[2]
    assert "8 seconds" in text and "unsaved work" in text and "is lost" in text
    assert question.call_args.args[-1] == QMessageBox.StandardButton.Cancel


def test_startup_and_live_state_show_age_and_long_session_warning(qapp, journal, monkeypatch):
    from modules.game_mode.game_mode_module import GameModeModule
    monkeypatch.setattr(session.time, "time", lambda: 100000)
    journal.begin()
    journal.data["started"] = 100000 - 7 * 3600 - 12 * 60
    m = GameModeModule()
    m._journal = journal
    banner = Mock()
    monkeypatch.setattr(m, "_show_error", banner)
    widget = m.create_widget()
    try:
        assert "ON for 7 h 12 min" in m._state.text()
        assert "still paused" in banner.call_args.args[0]
        assert "OFF is recommended" in banner.call_args.args[0]
    finally:
        widget.deleteLater()


def test_journal_writers_use_distinct_temporary_files(journal, monkeypatch):
    from modules.game_mode.engine import journal as journal_module
    os.makedirs(journal.folder, exist_ok=True)
    replace = journal_module.os.replace
    sources = []
    gate = threading.Barrier(2)
    replacement_lock = threading.Lock()
    def together(src, dst):
        sources.append(src)
        gate.wait(timeout=5)
        with replacement_lock:
            replace(src, dst)
    monkeypatch.setattr(journal_module.os, "replace", together)
    errors = []
    def write_one(value):
        try:
            Journal._write(journal.path, {"value": value})
        except Exception as error:
            errors.append(error)
    workers = [threading.Thread(target=write_one, args=(i,)) for i in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=6)
    assert not errors and len(set(sources)) == 2
    with open(journal.path, encoding="utf-8") as fh:
        assert json.load(fh)["value"] in (0, 1)


@pytest.mark.parametrize("payload", [[], {"actions": {}}, {"actions": None}])
def test_structurally_corrupt_journal_is_preserved(journal, payload):
    with open(journal.path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    assert journal.load() and journal.data.get("corrupt")
    session.turn_off(FakeSystem(), journal, _quiet, settle=0)
    with open(journal.path, encoding="utf-8") as fh:
        assert json.load(fh) == payload


@pytest.mark.parametrize("kind", ["service_stop", "service_start", "priority"])
def test_unreadable_restore_is_kept_for_retry(journal, monkeypatch, kind):
    sys_ = FakeSystem()
    journal.begin()
    entry = {"kind": kind, "name": "WSearch", "label": "Search"}
    if kind == "service_start":
        entry["name"] = catalog.VCACHE_SERVICE
    if kind == "priority":
        entry.update(pid=11, create_time=101, exe="chrome.exe", before=NORMAL)
        monkeypatch.setattr(sys_, "priority", lambda _pid: None)
    else:
        monkeypatch.setattr(sys_, "service", lambda _name: ServiceState(None, error="access denied"))
    journal.record(entry)
    session.turn_off(sys_, journal, _quiet, settle=0)
    assert journal.active and len(journal.actions) == 1


def test_on_does_not_report_unreadable_settings_as_already_set(journal, monkeypatch):
    sys_ = FakeSystem()
    monkeypatch.setattr(sys_, "reg_get", lambda *_: (None, None, 0))
    monkeypatch.setattr(sys_, "animations", lambda: None)
    monkeypatch.setattr(sys_, "service", lambda _name: ServiceState(None, error="access denied"))
    lines = []
    journal.begin()
    on = session._On(sys_, journal, lambda level, text: lines.append((level, text)))
    on.gamebar()
    on.visuals()
    on.vcache(True)
    assert not [text for level, text in lines if level == session.OK]
    assert journal.actions == []


def test_recovery_banner_survives_live_state_updates(qapp, journal, monkeypatch):
    from modules.game_mode import game_mode_module as ui
    monkeypatch.setattr(session.time, "time", lambda: 100000)
    journal.begin()
    journal.data["started"] = 100000 - 7 * 3600
    m = ui.GameModeModule()
    m._journal = journal
    widget = m.create_widget()
    monkeypatch.setattr(ui, "_keep_awake", lambda _on: None)
    monkeypatch.setattr(m, "_refresh_all", lambda: None)
    banner = Mock()
    monkeypatch.setattr(m, "_show_error", banner)
    try:
        text = "Recovery incomplete: 1 change(s) could not be put back -- Game Mode stays ON"
        m._off_done([{"level": session.BAD, "text": text}])
        m._show_state(100, 1)
        assert "Recovery incomplete" in banner.call_args.args[0]
    finally:
        widget.deleteLater()


def test_rejected_app_entry_is_dropped_even_if_same_exe_has_pending_relaunch(journal, monkeypatch):
    sys_ = FakeSystem()
    journal.begin()
    valid = journal.record({"kind": "app_close", "exe": "tool.exe", "label": "Tool",
                            "path": os.path.abspath("tool.exe"), "aumid": None, "reopen": True})
    journal.mark_done(valid)
    journal.record({"kind": "app_close", "exe": "tool.exe", "path": "relative.exe"})
    monkeypatch.setattr(sys_, "launch", lambda *_: None)
    session.turn_off(sys_, journal, _quiet, settle=0)
    assert journal.actions == [valid]


@pytest.mark.parametrize("kind", ["plan", "registry", "animations"])
def test_already_restored_settings_need_no_write(journal, monkeypatch, kind):
    sys_ = FakeSystem()
    journal.begin()
    if kind == "plan":
        entry = {"kind": kind, "before": ULTIMATE}
        method = "set_plan"
    elif kind == "registry":
        entry = {"kind": kind, "hive": "HKCU", "path": session.GAMEBAR_KEY,
                 "name": "AutoGameModeEnabled", "existed": True, "value": 1, "type": 4}
        method = "reg_set"
    else:
        entry = {"kind": kind, "before": True}
        method = "set_animations"
    journal.record(entry)
    changed = Mock(return_value="access denied")
    monkeypatch.setattr(sys_, method, changed)
    report = session.turn_off(sys_, journal, _quiet, settle=0)
    changed.assert_not_called()
    assert not journal.active and not [r for r in report if r["level"] == session.BAD]


@pytest.mark.parametrize("returncode", [0, 1])
def test_unreadable_plan_list_retains_recovery_entry(journal, monkeypatch, returncode):
    from modules.game_mode.engine import system as system_module
    journal.begin()
    entry = journal.record({"kind": "plan", "before": ULTIMATE})
    monkeypatch.setattr(system_module, "run", lambda *_args, **_kw:
                        Mock(returncode=returncode, stdout="", stderr="access denied"))
    report = session.turn_off(WinSystem(), journal, _quiet, settle=0)
    assert journal.active and journal.actions == [entry]
    assert any("Recovery incomplete" in r["text"] for r in report)
