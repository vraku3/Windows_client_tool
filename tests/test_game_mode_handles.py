"""Game Mode handle-count work (2026-10-19): agent hosts, idle launchers,
clean quits, the WM_CLOSE race, and the shell flyout host."""
import pytest

from modules.game_mode.engine import catalog, session
from modules.game_mode.engine.system import Proc

PATHS = {
    1: r"C:\Program Files\Microsoft VS Code\Code.exe",
    3: r"C:\Program Files (x86)\Steam\steam.exe",
    5: r"C:\Program Files (x86)\Battle.net\Battle.net.exe",
    7: r"C:\Users\u\AppData\Local\Programs\Teams\ms-teams.exe",
}


def _path(pid):
    return PATHS.get(pid, r"C:\Program Files\X\x.exe")


def _apps(procs):
    return {g.exe: g for g in session.running_apps(procs, 1, 999, _path)}


def test_a_program_running_an_agent_is_offered_but_never_preset():
    procs = [Proc(1, 0, "Code.exe", 7000, 1, 10), Proc(2, 1, "claude.exe", 300, 1, 11),
             Proc(7, 0, "ms-teams.exe", 4500, 1, 12)]
    groups = list(_apps(procs).values())
    code = next(g for g in groups if g.exe == "code.exe")
    assert code.hosts == ("claude.exe",) and "claude.exe" in code.label
    chosen = session.preset(catalog.MAX, groups)
    assert "code.exe" not in chosen.close and "ms-teams.exe" in chosen.close


def test_an_idle_steam_is_closable_with_its_helpers_counted():
    procs = [Proc(3, 0, "steam.exe", 1100, 1, 10), Proc(4, 3, "steamwebhelper.exe", 4700, 1, 11)]
    groups = list(_apps(procs).values())
    assert [g.exe for g in groups] == ["steam.exe"]
    assert session.freed_estimate(procs, groups, {"steam.exe"}) == 5800


def test_a_launcher_running_a_game_is_never_offered():
    procs = [Proc(3, 0, "steam.exe", 1100, 1, 10), Proc(6, 3, "game.exe", 900, 1, 12),
             Proc(5, 0, "Battle.net.exe", 4800, 1, 10)]
    apps = _apps(procs)
    assert "steam.exe" not in apps and "battle.net.exe" not in apps


def test_steam_is_asked_to_quit_by_its_own_command(monkeypatch):
    from modules.game_mode.engine import shell_launch, system as sysmod
    launched = []
    monkeypatch.setattr(shell_launch, "launch", lambda exe, args="", cwd="": launched.append((exe, args)))
    s = sysmod.WinSystem.__new__(sysmod.WinSystem)
    monkeypatch.setattr(s, "_matching", lambda ids: ids)
    monkeypatch.setattr(s, "_post_close", lambda ids: None)
    monkeypatch.setattr(s, "_wait_gone", lambda ids, t: True)
    monkeypatch.setattr(s, "_end_left", lambda group: None)
    s.close_many([([(3, 10)], PATHS[3])])
    assert launched == [(PATHS[3], "-shutdown")]


def test_a_window_closing_mid_enumeration_does_not_abort(monkeypatch):
    import pywintypes
    import win32gui
    import win32process
    from modules.game_mode.engine import system as sysmod
    s = sysmod.WinSystem.__new__(sysmod.WinSystem)
    monkeypatch.setattr(s, "_matching", lambda ids: ids)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (0, 3))
    posted = []

    def post(hwnd, msg, w, l):
        if hwnd == 1:
            raise pywintypes.error(1400, "PostMessage", "Invalid window handle.")
        posted.append(hwnd)
    monkeypatch.setattr(win32gui, "PostMessage", post)
    monkeypatch.setattr(win32gui, "EnumWindows", lambda cb, extra: [cb(h, extra) for h in (1, 2)])
    s._post_close([(3, 10)])
    assert posted == [2]


@pytest.mark.parametrize("exe", ["shellexperiencehost.exe"])
def test_the_flyout_host_is_closable_but_not_reopened(exe):
    assert not catalog.never_close(exe)
    assert exe in catalog.WINDOWS_ON_DEMAND
    assert catalog.spec_for(exe).reopen is False and catalog.spec_for(exe).level == catalog.MAX


class _QuietSystem:
    def __init__(self):
        self.launched, self.kept = [], []

    def processes(self):
        return []

    def launch(self, path, args="", cwd="", quiet=False):
        self.launched.append((path, quiet))

    def launch_aumid(self, aumid, quiet=False):
        self.launched.append((aumid, quiet))

    def keep_minimized(self, exes, seconds=15.0):
        self.kept.append(list(exes))


@pytest.mark.parametrize("window,quiet", [("minimized", True), ("hidden", True), ("normal", False)])
def test_an_app_comes_back_the_way_it_was(window, quiet):
    s = _QuietSystem()
    ctx = {"reopened": []}
    e = {"exe": "ms-teams.exe", "label": "Teams", "path": PATHS[7], "done": True, "reopen": True,
         "window": window}
    assert session._restore_app(s, e, lambda lvl, txt: None, ctx)
    assert s.launched == [(PATHS[7], quiet)]
    assert ctx.get("quiet", []) == (["ms-teams.exe"] if quiet else [])

