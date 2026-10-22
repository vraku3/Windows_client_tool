"""Regressions for the owner's 2026-10-21 maintenance run. No real maintenance."""
import os
from types import SimpleNamespace as NS

import pytest

from modules.updates import stage_runners as stages, windows_updater as wu
from modules.updates import winget_updater as wg
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult, scanners_system as ss


@pytest.fixture
def isolated_cleanup(tmp_path, monkeypatch):
    from modules.cleanup import cleanup_history
    from modules.cleanup.cleanup_scanner import exclusions, scan_cache, catalog
    monkeypatch.setattr(cleanup_history, "record_report", lambda *a, **k: None)
    monkeypatch.setattr(exclusions, "list_exclusions", lambda: [])
    monkeypatch.setattr(catalog, "scanners_for", lambda *a, **k: {})
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA", "windir"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    scan_cache.invalidate()
    yield
    scan_cache.invalidate()


def test_temp_keeps_whole_profile_subtree_and_cleans_sibling(tmp_path, isolated_cleanup):
    profile = tmp_path / "temp" / "test-tree" / "Default"
    store = profile / "Local Storage"
    store.mkdir(parents=True)
    (store / "data").write_bytes(b"login")
    (profile.parent / "also-keep.tmp").write_bytes(b"keep")
    junk = tmp_path / "temp" / "old.tmp"
    junk.write_bytes(b"discard")
    report, refused, _ = ss.delete_items_report([ScanItem(str(junk.parent), 16, True)])
    assert not junk.exists() and report.freed == 7
    assert (profile.parent / "also-keep.tmp").exists()
    assert not refused
    assert report.protected and "sign-in" in report.protected[0][1]
    assert ss._refusal_reason(ScanItem(str(store), 5, True))
    assert ss._refusal_reason(ScanItem(str(store / "data"), 5, False))
    assert ss._refusal_reason(ScanItem(str(junk.parent), 9, True, keep_root=False))


@pytest.mark.parametrize("preexisting,rc", [(True, 0), (False, 2148468742), (False, -2146498554)])
def test_dism_pending_operations_are_skipped(monkeypatch, preexisting, rc):
    from core import pending_reboot
    import subprocess
    calls = []
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=preexisting))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or NS(returncode=rc, stdout="pending", stderr=""))
    lines = []
    result = stages.run_dism_stage(None, lines.append, lambda: False)
    assert result["status"] == "SKIPPED" and result["reboot_required"]
    assert not result.get("error") and "restart is pending" in " ".join(lines)
    assert bool(calls) is not preexisting


def test_large_size_explains_package_bound_and_downloaded_has_no_size():
    u = wu.WindowsUpdate("KB1", "T", "c", 92638, "d")
    assert "size not known in advance" in wu.size_text(u)
    assert "90" in wu.size_text(u) and "GB" in wu.size_text(u)
    u.downloaded = True
    assert wu.size_text(u) == "already downloaded"


def test_cleanup_summary_explains_all_outcomes():
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    report = DeleteReport(locked=["locked"], denied=["denied", "guard"], kept_recent=2, cloud_only=3)
    report.protected = [("guard", "sign-in data")]
    text = stages.cleanup_left_text(report, [("root", "protected state")])
    for phrase in ("1 in use", "2 kept", "24 h", "1 access denied", "3 cloud-only", "2 protected/refused", "sign-in data"):
        assert phrase in text


def test_cleanup_uses_catalog_cache_light_selection_and_dedup(tmp_path, isolated_cleanup, monkeypatch):
    from modules.cleanup import cleanup_scanner as cs
    from modules.cleanup.cleanup_scanner import catalog, scan_cache
    from modules.cleanup import cleanup_presets
    root = tmp_path / "cache"
    root.mkdir()
    (root / "a").write_bytes(b"1234")
    calls, selection, categories = [], [], []
    def handmade(min_age_days=0):
        return ScanResult([ScanItem(str(root), 4, True)])
    def extra(min_age_days=0):
        return ScanResult([ScanItem(str(root / "a"), 4, False)])
    def danger(min_age_days=0):
        pytest.fail("caution scanner must not run")
    monkeypatch.setattr(cs, "_OV_GROUPS", [("Handwritten", [handmade])])
    def scanners(category, present_only=False):
        categories.append((category, present_only))
        return {extra: ("Extra", "safe"), danger: ("Danger", "caution")} if category == "dev" else {}
    monkeypatch.setattr(catalog, "scanners_for", scanners)
    original = scan_cache.cached_scan
    monkeypatch.setattr(scan_cache, "cached_scan", lambda fn, age: calls.append(fn) or original(fn, age))
    select = cleanup_presets.items_for_preset
    monkeypatch.setattr(cleanup_presets, "items_for_preset", lambda preset, *a: selection.append(preset) or select(preset, *a))
    lines = []
    result = stages.run_cleanup_safe_stage(None, lines.append, lambda: False)
    assert result["freed"] == 4 and result["deleted"] == 1
    assert {handmade, extra} == set(calls) and selection == ["light"]
    assert set(categories) == {(c, True) for c in catalog.CATEGORY_TABS}
    assert result["areas"] and any("Handwritten" in line for line in lines)


def test_location_retry_uses_existing_folder_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(wg, "find_install_location", lambda package: str(tmp_path))
    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "list":
            return "Name   Id         Version  Available Source\n------------------------------------------\nApp    Acme.App   2.0                winget\n"
        return "installed" if "--location" in args else "This package requires an install location"
    monkeypatch.setattr(wg, "_run_winget", run)
    result = wg.install_update("Acme.App", lambda _: None, target_version="2.0")
    assert result.state == "UPDATED" and len(calls) == 3
    assert calls[1][-2:] == ["--location", str(tmp_path)]


def test_user_scope_retry_requires_task_result_and_version_readback(monkeypatch):
    calls = []
    monkeypatch.setattr(wg, "_run_user_upgrade", lambda package, *a: calls.append(package) or (0, "", ""))
    monkeypatch.setattr(wg, "_run_winget", lambda args, **k: (
        "cannot be uninstalled when running with administrator privileges" if args[0] == "upgrade" else
        "Name   Id         Version  Available Source\n------------------------------------------\nApp    Acme.App   2.0                winget\n"))
    result = wg.install_update("Acme.App", lambda _: None, target_version="2.0")
    assert result.state == "UPDATED" and calls == ["Acme.App"]
    monkeypatch.setattr(wg, "_run_user_upgrade", lambda package, *a: (None, "timed out", ""))
    result = wg.install_update("Acme.App", lambda _: None, target_version="2.0")
    assert result.state == "UNCONFIRMED" and "timed out" in result.detail


def test_task_path_and_latest_report(tmp_path, monkeypatch):
    from modules.updates import task_health as th
    monkeypatch.setattr(th.subprocess, "run", lambda *a, **k: NS(returncode=0, stderr="", stdout=(
        'Status: Ready\nTask To Run: "C:\\Old Copy\\app.exe" --unattended --stages wu\nLast Run Time: today\nLast Result: 0\n')))
    health = th.check_task_health("task")
    assert health.exe_path == r"C:\Old Copy\app.exe"
    assert th.task_copy_mismatch(health, r"C:\New Copy\app.exe")
    updates = tmp_path / "updates"
    updates.mkdir()
    report = updates / "report-20261021-220000.html"
    report.write_text("report")
    assert th.latest_report(str(tmp_path)) == str(report)


def test_unattended_notification_counts_confirmed_and_restart():
    from modules.updates.unattended_runner import completion_message
    message = completion_message({"wu_installed": 1, "wu_pending_restart": 2,
        "winget_results": [{"confirmed": True}, {"state": "NEEDS INPUT"}]}, "report.html", True)
    assert "Windows installed: 1" in message and "apps confirmed: 1" in message
    assert "Windows awaiting restart: 2" in message and "apps needing you / broken: 1" in message
    assert "restart to finish" in message and "report: report.html" in message


def test_wu_stage_separates_restart_from_installed_totals(monkeypatch, tmp_path):
    from modules.updates import history_writer, report_generator
    pending = wu.WindowsUpdate("KB1", "Already staged", "c", 90000, "d", pending_restart=True)
    new = wu.WindowsUpdate("KB2", "New", "c", 1, "d")
    monkeypatch.setattr(wu, "fetch_pending_updates", lambda **kwargs: [pending, new])
    calls = []
    def install(updates, **kwargs):
        calls.extend(updates)
        return [wu.InstallResult("KB2", "New", False, 0, "installed -- restart to finish", True)]
    monkeypatch.setattr(wu, "install_updates_iter", install)
    monkeypatch.setattr("core.disk_space.check_wu_preflight", lambda size: None)
    monkeypatch.setattr("core.windows_utils.is_reboot_pending", lambda: False)
    app = NS(config=NS(get=lambda key, default=None: False if "restore_point" in key else default))
    lines = []
    result = stages.run_wu_stage(app, lines.append, lambda: False)
    assert calls == [new] and result["installed_count"] == 0 and result["pending_restart_count"] == 2
    assert result["reboot_required"] and stages.stage_failures("wu", result) == []
    assert lines[-1] == "Restart required to finish 2 update(s)"
    normalized = stages.normalize_stage_data({"wu": result})
    history_writer.append_run(str(tmp_path), updates_installed=normalized["wu_installed"],
                              pending_restart=normalized["wu_pending_restart"])
    history = history_writer.load_history(str(tmp_path))
    assert history[-1]["updates"] == 0 and history[-1]["pending_restart"] == 2
    html = report_generator.render_update_report_html(normalized, history)
    assert "Restart required to finish 2 update(s)" in html and "0/2 installed" in html


def test_task_settings_warn_and_update_to_running_copy(tmp_path, monkeypatch):
    import sys
    from modules.updates import task_health as th, updates_module as ui
    health = th.TaskHealth(exists=True, enabled=True, exe_path=r"C:\Old Copy\app.exe",
                           last_run="today", last_result_code=0, last_result_text="success")
    monkeypatch.setattr(th, "check_task_health", lambda name: health)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Current Copy\app.exe")
    calls = []
    monkeypatch.setattr(ui.subprocess, "run", lambda args, **k: calls.append(args) or NS(returncode=0, stderr=""))
    config = NS(get=lambda key, default=None: default, set=lambda *a: None, save=lambda: True)
    tab = ui._UpdateSettingsTab()
    tab.set_app(NS(config=config, app_data_dir=str(tmp_path)))
    assert not tab._task_copy_btn.isHidden() and "another copy" in tab._task_copy_lbl.text()
    assert "today" in tab._task_health_lbl.text() and "success" in tab._task_health_lbl.text()
    tab._task_copy_btn.click()
    assert calls and calls[0][calls[0].index("/tr") + 1].startswith('"C:\\Current Copy\\app.exe" --unattended')
    tab.deleteLater()


def test_arp_location_requires_unambiguous_existing_folder(tmp_path, monkeypatch):
    location = tmp_path / "Battle.net"
    location.mkdir()
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    rows = [{"key": "Blizzard.BattleNet", "DisplayName": "Battle.net", "InstallLocation": str(location)}]
    monkeypatch.setattr(wg, "_arp_entries", lambda: iter(rows))
    assert wg.find_install_location("Blizzard.BattleNet") == os.path.normcase(str(location))
    rows[0]["InstallLocation"] = str(tmp_path / "absent")
    assert wg.find_install_location("Blizzard.BattleNet") is None


def test_notification_starts_hidden_without_wait(tmp_path, monkeypatch):
    import base64
    from modules.updates import maintenance_notify
    calls = []
    monkeypatch.setattr(maintenance_notify.subprocess, "Popen", lambda args, **k: calls.append((args, k)))
    maintenance_notify.notify_finished("Maintenance finished -- report: C:\\User's\\report.html", str(tmp_path))
    args, kwargs = calls[0]
    assert args[args.index("-WindowStyle") + 1] == "Hidden"
    script = base64.b64decode(args[-1]).decode("utf-16-le")
    assert "$env:WCT_NOTIFY_TEXT" in script and "ShowBalloonTip" in script
    assert "User's" in kwargs["env"]["WCT_NOTIFY_TEXT"]
    assert kwargs["creationflags"] == maintenance_notify.subprocess.CREATE_NO_WINDOW


def test_wu_verification_preserves_install_result_restart_requirement():
    from PyQt6.QtCore import Qt
    from modules.updates.updates_module import _WinUpdatesTab

    # A follow-up COM read can lag behind the installation result.
    offered = wu.WindowsUpdate("KB5127648", "Security update", "c", 1, "d")
    result = wu.InstallResult(offered.kb, offered.title, False, 0,
                              "installed -- restart to finish", True)
    tab = _WinUpdatesTab()
    try:
        tab._on_verify_done([result], {"updates": [offered], "held": []})
        assert offered.pending_restart
        assert not tab._install_btn.isEnabled()
        assert not (tab._table.item(0, 0).flags() & Qt.ItemFlag.ItemIsSelectable)
        assert "restart to finish" in tab._status_lbl.text()
    finally:
        tab.deleteLater()


def test_cleanup_light_uses_effective_research_safety(tmp_path, isolated_cleanup, monkeypatch):
    from modules.cleanup import cleanup_scanner as cs, cleanup_presets
    from modules.cleanup.cleanup_scanner import catalog, knowledge

    cache = tmp_path / "review-cache"
    cache.mkdir()
    junk = cache / "recoverable-data"
    junk.write_bytes(b"keep")

    def scan_review_cache(min_age_days=0):
        pytest.fail("A catalog entry with effective caution safety must not be scanned")

    scan_review_cache.spec = NS(id="review_cache", disabled_reason="", safety="safe")
    monkeypatch.setattr(cs, "_OV_GROUPS", [])
    monkeypatch.setattr(catalog, "scanners_for", lambda category, **k: (
        {scan_review_cache: ("Reviewed cache", "safe")} if category == "dev" else {}))
    monkeypatch.setattr(knowledge, "entry_for", lambda scanner_id: knowledge.Knowledge(verdict="caution"))
    result = ScanResult([ScanItem(str(cache), 4, True)])
    assert cleanup_presets.items_for_preset("light", {"review": result},
                                          {"review": "scan_review_cache"}) == []
    assert stages.run_cleanup_safe_stage(None, lambda line: None, lambda: False)["freed"] == 0
    assert junk.read_bytes() == b"keep"
