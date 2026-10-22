"""Review 7 regressions. All external maintenance operations are replaced."""
import base64
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core import winget_list
from modules.updates import winget_updater as wg, windows_updater as wu
from modules.updates import stage_runners as stages


@pytest.mark.parametrize("scope,retry", [("user", False), ("machine", False), ("user", True)])
def test_portable_empty_folder_is_broken(tmp_path, monkeypatch, scope, retry):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    root = tmp_path / ("local/Microsoft" if scope == "user" else "pf") / "WinGet"
    (root / "Packages/Acme.App_winget").mkdir(parents=True)
    def run(args, **kw):
        if args[0] == "upgrade":
            return winget_list.PER_USER_REFUSAL if retry else "success"
        return ("Name   Id         Version  Available Source\n------------------------------------------\n"
                "App    Acme.App   2.0                winget\n")
    monkeypatch.setattr(wg, "_run_winget", run)
    monkeypatch.setattr(wg, "_run_user_upgrade", lambda *a: (0, "", "success"))
    result = wg.install_update("Acme.App", lambda _: None, target_version="2.0")
    assert result.state == "BROKEN" and not result
    assert f"--scope {scope} --force" in result.detail
    assert stages.stage_failures("winget", {"results": [{"state": "BROKEN", "id": "Acme.App"}]})


def test_portable_link_must_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "Microsoft/WinGet"
    package = root / "Packages/Acme.App_winget"
    package.mkdir(parents=True)
    exe = package / "App.exe"
    exe.write_bytes(b"exe")
    links = root / "Links"
    links.mkdir()
    link = links / "App.exe"
    link.write_bytes(b"link")
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda p: str(exe) if str(p) == str(link) else realpath(p))
    snapshot = winget_list.portable_snapshot("Acme.App")
    assert winget_list.portable_problem(snapshot, "Acme.App", "2") is None
    (links / "App.exe").unlink()
    assert "program files are missing" in winget_list.portable_problem(snapshot, "Acme.App", "2")


def test_retry_waits_for_staged_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    staged = tmp_path / "WinGet/Acme.App.2"
    staged.mkdir(parents=True)
    (staged / "app.exe").write_bytes(b"exe")
    polls, sleeps, lines = [], [], []
    clock = [0]
    monkeypatch.setattr(wg, "_exclusive_available", lambda path: polls.append(path) or len(polls) >= 3)
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(wg.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(wg.time, "sleep", sleep)
    assert wg._wait_staged_installer("Acme.App", "2", lines.append)
    assert len(sleeps) == 2 and "2 s" in lines[-1]
    monkeypatch.setattr(wg, "_exclusive_available", lambda path: False)
    assert not wg._wait_staged_installer("Acme.App", "2", lines.append)
    assert "60 s" in lines[-1]


def test_locked_stage_never_launches_retry(monkeypatch):
    monkeypatch.setattr(wg, "_run_winget", lambda *a, **k: winget_list.PER_USER_REFUSAL)
    monkeypatch.setattr(wg, "_wait_staged_installer", lambda *a: False)
    monkeypatch.setattr(wg, "_run_user_upgrade", lambda *a: pytest.fail("must not launch"))
    result = wg.install_update("Acme.App", lambda _: None, target_version="2")
    assert result.state == "NEEDS USER" and "still held" in result.detail


def test_battlenet_exact_display_name(tmp_path, monkeypatch):
    folder = tmp_path / "Battle.net"
    folder.mkdir()
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setattr(wg, "_arp_entries", lambda: iter([{
        "key": "Battle.net", "DisplayName": "Battle.net", "InstallLocation": str(folder)}]))
    assert wg.find_install_location("Blizzard.BattleNet", "Battle.net") == os.path.normcase(str(folder))
    assert wg.find_install_location("Blizzard.BattleNet", "Battle") is None


def test_battlenet_two_arp_matches_are_ambiguous(tmp_path, monkeypatch):
    folder = tmp_path / "Battle.net"
    folder.mkdir()
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setattr(wg, "_arp_entries", lambda: iter([
        {"key": key, "DisplayName": "Battle.net", "InstallLocation": str(folder)}
        for key in ("Battle.net", "Other Battle.net")]))
    assert wg.find_install_location("Blizzard.BattleNet", "Battle.net") is None


def test_unrelated_link_on_other_drive_does_not_hide_portable(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    package = tmp_path / "Microsoft/WinGet/Packages/Acme.App_winget"
    package.mkdir(parents=True)
    links = tmp_path / "Microsoft/WinGet/Links"
    links.mkdir()
    (links / "other.exe").write_bytes(b"link")
    def other_drive(_):
        raise ValueError("Paths on different drives")
    monkeypatch.setattr(os.path, "commonpath", other_drive)
    snapshot = winget_list.portable_snapshot("Acme.App")
    assert len(snapshot) == 1
    assert winget_list.portable_problem(snapshot, "Acme.App", "2")


@pytest.mark.parametrize("code", [1, 3, 4, 5])
def test_failed_wu_result_never_awaits_restart(code):
    assert not wu.InstallResult("KB1", "T", False, 0, "failed", True, code).pending_restart


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5])
def test_only_successful_wu_history_counts_as_waiting(monkeypatch, code):
    from datetime import datetime, timezone
    from core import pending_reboot
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=True))
    entry = NS(Operation=1, ResultCode=code, Date=datetime.now(timezone.utc), UpdateIdentity=NS(UpdateID="offer"))
    searcher = NS(GetTotalHistoryCount=lambda: 1, QueryHistory=lambda *a: NS(Count=1, Item=lambda i: entry))
    assert wu._pending_restart_ids(searcher) == ({"offer"} if code == 2 else set())


def test_failed_wu_attempt_stays_selectable_despite_lagging_scan():
    from modules.updates.updates_module import _WinUpdatesTab
    tab = _WinUpdatesTab()
    try:
        offered = wu.WindowsUpdate("KB1", "T", "c", 1, "d", pending_restart=True)
        result = wu.InstallResult("KB1", "T", False, 1, "failed", True, 3)
        tab._on_verify_done([result], {"updates": [offered], "held": []})
        assert not offered.pending_restart and tab._install_btn.isEnabled()
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("minimum,expected", [(0, 10240), (12000, 12000)])
def test_large_download_has_conservative_nonzero_budget(minimum, expected):
    warnings = []
    update = wu.WindowsUpdate("KB", "T", "C", 90000, "", min_size_mb=minimum)
    assert wu.download_size_mb([update], warnings.append) == expected
    assert "size uncertain" in warnings[0]


def test_notification_message_is_data(tmp_path, monkeypatch):
    from modules.updates import maintenance_notify as notify
    calls = []
    monkeypatch.setattr(notify.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    text = "\u2019,0);calc;#"
    notify.notify_finished(text, str(tmp_path))
    notify.notify_finished("ordinary", str(tmp_path))
    assert calls[0][0] == calls[1][0]
    assert calls[0][1]["env"]["WCT_NOTIFY_TEXT"] == text
    script = base64.b64decode(calls[0][0][0][-1]).decode("utf-16-le")
    assert "$env:WCT_NOTIFY_TEXT" in script and text not in script


def test_summary_separates_counts_and_unknowns(tmp_path):
    from modules.updates import history_writer as history, report_generator as report
    data = stages.normalize_stage_data({"winget": {"results": [
        {"confirmed": True}, {"state": "BROKEN"}]}, "machine_restart_pending": True})
    assert data["apps_confirmed"] == 1 and data["apps_need_you"] == 1
    assert data["wu_installed"] is None and data["machine_restart_pending"] is True
    history.append_run(str(tmp_path), winget_count=1, apps_need_you=1, machine_restart_pending=True)
    record = history.load_history(str(tmp_path))[-1]
    assert record["wg"] == 1 and record["apps_need_you"] == 1 and record["machine_restart_pending"] is True
    html = report.render_update_report_html({"dism_output": "DISM header\n[==== 42.0% ====]\nError: refused\nFinal status"}, [{"ts": "old"}])
    assert "42.0%" not in html and "Error: refused" in html and "Final status" in html
    assert "<td>-</td>" in html


def test_final_rescan_records_failures_and_remaining(monkeypatch):
    from core import pending_reboot
    monkeypatch.setattr(wu, "fetch_pending_updates", lambda **k: [
        wu.WindowsUpdate("KB1", "Remaining Windows", "c", 1, "d")])
    monkeypatch.setattr(wg, "fetch_updates", lambda **k: [wg.AppUpdate("Remaining App", "Acme.App", "1", "2", "winget")])
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=True))
    data = {"store": {"triggered": 1}, "winget": {"results": [{"id": "Acme.App", "state": "BROKEN"}]}}
    stages.finalize_run(data)
    text = stages.outstanding_text(data["outstanding"])
    assert "1 Windows updates" in text and "Remaining Windows" in text
    assert "Remaining App (BROKEN)" in text and "restart pending: yes" in text
    assert "scan triggered, not verified" in text
    monkeypatch.setattr(wu, "fetch_pending_updates", lambda **k: (_ for _ in ()).throw(RuntimeError("offline")))
    stages.finalize_run(data)
    assert "Windows updates: could not check" in stages.outstanding_text(data["outstanding"])


@pytest.mark.parametrize("after_launch", [False, True])
def test_monitoring_loss_preserves_launch_status(monkeypatch, after_launch):
    import win32com.client
    from core import limited_task
    class Entry:
        def Run(self, _):
            return None
        @property
        def State(self):
            raise RuntimeError("monitor lost")
    def register(*a):
        if not after_launch:
            raise RuntimeError("registration failed")
        return Entry()
    definition = NS(Principal=NS(), Settings=NS(), RegistrationInfo=NS(), Actions=NS(Create=lambda _: NS()))
    folder = NS(GetTasks=lambda _: [], RegisterTaskDefinition=register, DeleteTask=lambda *a: None)
    service = NS(Connect=lambda: None, GetFolder=lambda _: folder, NewTask=lambda _: definition)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: service)
    monkeypatch.setattr(limited_task, "_current_user_sid", lambda: "sid")
    rc, reason = limited_task.run_limited("winget.exe", [])
    assert rc is None
    assert reason.startswith("STILL RUNNING:" if after_launch else "failed before launch:")


def test_user_console_capture_and_diagnostics(tmp_path, monkeypatch):
    from core import limited_task
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(wg.shutil, "which", lambda _: r"C:\Apps\winget.exe")
    diagnostics = tmp_path / "local/Packages/Microsoft.DesktopAppInstaller_8wekyb3d8bbwe/LocalState/DiagOutputDir"
    diagnostics.mkdir(parents=True)
    lines, actions = [], []
    def run(exe, args, **k):
        actions.append((exe, args))
        output = next((tmp_path / "WindowsTweaker/updates").glob("winget-user-*.log"), None)
        assert output is None
        import re
        path = re.search(r"Out-File -Encoding utf8 '([^']+)'", args[-1])[1]
        Path(path).write_bytes(b"x" * 70000 + b"\ninstaller needs a restart")
        (diagnostics / "WinGet-new.log").write_text("\n".join(f"diag{i}" for i in range(50)))
        return 3010, ""
    monkeypatch.setattr(limited_task, "run_limited", run)
    rc, reason, output = wg._run_user_upgrade("Acme.App", None, lines.append)
    assert rc == 3010 and not reason and "restart" in output and len(output) <= 65536
    assert os.path.isabs(actions[0][0]) and "powershell" in actions[0][0].lower()
    script = actions[0][1][-1]
    assert "--log" not in script and "; exit $LASTEXITCODE" in script
    diagnostic = next(line for line in lines if "WinGet-new.log" in line)
    assert "diag10\n" in diagnostic and "diag9\n" not in diagnostic and "diag49" in diagnostic


def test_missing_console_is_explicit(tmp_path, monkeypatch):
    from core import limited_task
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(wg.shutil, "which", lambda _: r"C:\Apps\winget.exe")
    monkeypatch.setattr(limited_task, "run_limited", lambda *a, **k: (9, ""))
    assert wg._run_user_upgrade("Acme.App")[2] == "no output captured"


@pytest.mark.parametrize("entrypoint", ["gui", "unattended"])
def test_run_entrypoints_save_final_inventory_and_separate_counts(tmp_path, monkeypatch, entrypoint):
    from app import App
    from core import pending_reboot
    from modules.updates import run_all_tab, unattended_runner, history_writer, maintenance_notify
    app = NS(app_data_dir=str(tmp_path), config=NS(get=lambda key, default=None: default), shutdown=lambda: None)
    order = []
    raw = "DISM header\n[====== 42.0% ======]\nThe operation completed successfully."
    def winget_stage(app, log, cancelled):
        order.append("install")
        return {"results": [{"name": "Broken app", "id": "Acme.App", "state": "BROKEN", "confirmed": False}]}
    def dism_stage(app, log, cancelled):
        log(raw)
        return {"output": raw}
    def wu_scan(**kwargs):
        order.append("final scan")
        assert kwargs == {"include_hidden": False, "include_drivers": True}
        return []
    monkeypatch.setitem(stages.STAGE_RUNNERS, "wu", lambda *a: {"installed_count": 1, "pending_restart_count": 2})
    monkeypatch.setitem(stages.STAGE_RUNNERS, "winget", winget_stage)
    monkeypatch.setitem(stages.STAGE_RUNNERS, "store", lambda *a: {"triggered": 1})
    monkeypatch.setitem(stages.STAGE_RUNNERS, "dism", dism_stage)
    monkeypatch.setattr(wu, "fetch_pending_updates", wu_scan)
    monkeypatch.setattr(wg, "fetch_updates", lambda: [])
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=True))
    monkeypatch.setattr("core.disk_space.check_report_preflight", lambda path: None)
    if entrypoint == "unattended":
        monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
        monkeypatch.setattr(App, "instance", app)
        notifications = []
        monkeypatch.setattr(maintenance_notify, "notify_finished", lambda message, _: notifications.append(message))
        assert unattended_runner.run_unattended(["wu", "winget", "store", "dism"]) == 1
        assert "INCOMPLETE" in notifications[0] and "apps needing you / broken: 1" in notifications[0]
    else:
        monkeypatch.setattr(run_all_tab.QThreadPool, "globalInstance", lambda: NS(start=lambda w: w.run()))
        monkeypatch.setattr(os, "startfile", lambda path: None)
        tab = run_all_tab._RunAllTab()
        try:
            tab.set_app(app)
            tab._chk_cleanup.setChecked(False)
            tab._chk_dism.setChecked(True)
            tab._do_run()
            assert tab._log.toPlainText().startswith("Still outstanding")
            assert "incomplete" in tab._status_lbl.text().lower()
        finally:
            tab.deleteLater()
    assert order == ["install", "final scan"]
    record = history_writer.load_history(str(tmp_path))[-1]
    assert (record["updates"], record["pending_restart"], record["apps_confirmed"], record["apps_need_you"]) == (1, 2, 0, 1)
    assert record["machine_restart_pending"] is True
    session = next((tmp_path / "updates").glob("session-*.log")).read_text(encoding="utf-8")
    assert session.startswith("Still outstanding") and raw in session
    html = next((tmp_path / "updates").glob("report-*.html")).read_text(encoding="utf-8")
    assert html.index("Still outstanding") < html.index('<div class="cards">')
    assert "42.0%" not in html and "Broken app (BROKEN)" in html
