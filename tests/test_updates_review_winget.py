from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import os
import re

import pytest

from modules.updates import winget_updater as wg, stage_runners as stages


@pytest.mark.parametrize("match,inside,escape,expected", [
    ("key", True, False, True), ("WinGetPackageIdentifier", True, False, True),
    ("DisplayName", True, False, False), ("key", False, False, False),
    ("key", True, True, False)])
def test_arp_location_requires_id_and_real_programfiles(tmp_path, monkeypatch, match, inside, escape, expected):
    pf = tmp_path / "Program Files"
    folder = (pf if inside else tmp_path / "user") / "App"
    folder.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files (x86)"))
    row = {match: "App" if match == "DisplayName" else "Acme.App", "InstallLocation": str(folder)}
    monkeypatch.setattr(wg, "_arp_entries", lambda: iter([row]))
    if escape:
        realpath = os.path.realpath
        monkeypatch.setattr(wg.os.path, "realpath", lambda path: str(tmp_path / "user" / "App")
                            if str(path) == str(folder) else realpath(path))
    assert bool(wg.find_install_location("Acme.App")) is expected


def test_arp_reads_only_machine_hive(monkeypatch):
    import winreg
    hives = []
    def open_key(hive, *a):
        hives.append(hive)
        raise FileNotFoundError()
    monkeypatch.setattr(winreg, "OpenKey", open_key)
    assert list(wg._arp_entries()) == []
    assert hives == [winreg.HKEY_LOCAL_MACHINE] * 2


def test_user_upgrade_arguments_and_capped_log(tmp_path, monkeypatch):
    from core import limited_task
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(wg.shutil, "which", lambda _: r"C:\WindowsApps\winget.exe")
    calls, lines = [], []
    def task(exe, args, **kwargs):
        calls.append(args)
        path = re.search(r"Out-File -Encoding utf8 '([^']+)'", args[-1])[1]
        with open(path, "wb") as f:
            f.write(b"x" * 70000 + b"\nrestart needed\n")
        return 3010, ""
    monkeypatch.setattr(limited_task, "run_limited", task)
    rc, reason, output = wg._run_user_upgrade("Acme.App", r"C:\Program Files\App", lines.append)
    assert rc == 3010 and not reason and "restart needed" in output
    assert len(output) <= 65536
    assert "'--include-unknown'" in calls[0][-1]
    assert "'--location' 'C:\\Program Files\\App'" in calls[0][-1]
    log = re.search(r"Out-File -Encoding utf8 '([^']+)'", calls[0][-1])[1]
    assert os.path.dirname(log) == str(tmp_path / "WindowsTweaker" / "updates")
    assert "winget-user-Acme.App-" in log and any(log in line for line in lines)


@pytest.mark.parametrize("output,state,detail", [
    ("installer requires a restart", "UNCONFIRMED", "restart"),
    ("Specify the install root:", "NEEDS INPUT", "needs an answer"),
    ("installer failed", "FAILED", "exit 99")])
def test_user_retry_preserves_location_output_exit_and_scope(monkeypatch, output, state, detail):
    calls, tasks = [], []
    monkeypatch.setattr(wg, "find_install_location", lambda _: r"C:\Program Files\App")
    def run(args, **kw):
        calls.append(args)
        if args[0] == "list":
            return ("Name   Id         Version  Available Source\n------------------------------------------\n"
                    "App    Acme.App   1.0      2.0       winget\n")
        return ("This package requires an install location" if "--location" not in args else
                "cannot be uninstalled when running with administrator privileges")
    monkeypatch.setattr(wg, "_run_winget", run)
    monkeypatch.setattr(wg, "_run_user_upgrade", lambda *a, **k: tasks.append((a, k)) or (99, "", output))
    result = wg.install_update("Acme.App", lambda _: None, target_version="2.0", before_version="1.0")
    assert result.state == state and detail in result.detail
    assert r"C:\Program Files\App" in tasks[0][0]
    if state != "NEEDS INPUT":
        assert calls[-1][calls[-1].index("--scope") + 1] == "user"


def test_limited_task_prunes_only_old_root_prefix(monkeypatch):
    from core import limited_task
    import win32com.client
    now = datetime.now(timezone.utc)
    entries = [NS(Name=name, Definition=NS(RegistrationInfo=NS(Date=when.isoformat()))) for name, when in [
        ("WinClientTool_OneShot_old", now - timedelta(hours=2)),
        ("WinClientTool_OneShot_fresh", now), ("Other_old", now - timedelta(days=1))]]
    deleted = []
    folder = NS(GetTasks=lambda flags: entries, DeleteTask=lambda name, _: deleted.append(name))
    definition = NS(Principal=NS(), Settings=NS(), RegistrationInfo=NS(), Actions=NS(Create=lambda _: NS()))
    folder.RegisterTaskDefinition = lambda *a: NS(Run=lambda _: None, State=3, LastTaskResult=0)
    service = NS(Connect=lambda: None, GetFolder=lambda _: folder, NewTask=lambda _: definition)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: service)
    monkeypatch.setattr(limited_task, "_current_user_sid", lambda: "sid")
    assert limited_task.run_limited("winget.exe", ["upgrade"])[0] == 0
    assert deleted[0] == "WinClientTool_OneShot_old" and len(deleted) == 2
    assert definition.RegistrationInfo.Date


def test_user_timeout_stops_stage_and_marks_rest_skipped(monkeypatch):
    monkeypatch.setattr(wg, "fetch_updates", lambda **k: [
        wg.AppUpdate("App", "Acme.App", "1", "2", "winget"),
        wg.AppUpdate("Other", "Acme.Other", "1", "2", "winget")])
    calls = []
    monkeypatch.setattr(wg, "_run_winget", lambda *a, **k: calls.append(a) or
                        "cannot be uninstalled when running with administrator privileges")
    monkeypatch.setattr(wg, "_run_user_upgrade", lambda *a, **k:
                        (None, "STILL RUNNING: installer still running after 10 minutes", ""))
    app = NS(config=NS(get=lambda key, default: default))
    result = stages.run_winget_stage(app, lambda _: None, lambda: False)
    assert [r["state"] for r in result["results"]] == ["STILL RUNNING", "SKIPPED"]
    assert len(calls) == 1 and "still running" in result["results"][1]["after"]
    assert stages.stage_failures("winget", result)
