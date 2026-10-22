"""Focused regressions for Optimise OS cleanup; no real machine mutations."""
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from modules.game_mode.engine import space as sp
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult
from modules.cleanup.cleanup_scanner.deleter import DeleteReport


class Backend:
    def __init__(self):
        self.items = {}
        self.deleted = []
        self.busy = (False, "")
        self.stage = {"returncode": 0, "output": "done"}

    def scan(self, name, age):
        return ScanResult(self.items.get(name, []))

    def servicing_busy(self):
        return self.busy

    def delete(self, items, stop, cancelled):
        self.deleted.extend(items)
        parts = [(i.path, DeleteReport(freed=i.size, files=1)) for i in items]
        return DeleteReport(freed=sum(i.size for i in items), files=len(items)), [], parts

    def dism(self, log, cancelled):
        if isinstance(self.stage, Exception):
            raise self.stage
        return self.stage


@pytest.mark.parametrize("busy", [(True, "installing"), (None, "unreadable")])
def test_only_wu_obeys_installer_busy(busy):
    b = Backend()
    b.busy = busy
    result = sp.SpaceCleaner(b).clean({"wu_cache", sp.DISM_ID})
    assert set(result.skipped) == {"wu_cache"}
    assert result.dism == b.stage


@pytest.mark.parametrize("stage", [subprocess.TimeoutExpired("dism", 1800), OSError("launch denied")])
def test_dism_exception_preserves_file_cleanup(stage):
    b = Backend()
    b.items["scan_temp_files"] = [ScanItem(r"C:\Temp", 15, True)]
    b.stage = stage
    result = sp.SpaceCleaner(b).clean({"temp", sp.DISM_ID})
    assert result.freed == 15 and result.files == 1
    assert result.failed and result.dism["error"]
    assert "15 B" in result.text()


def test_dism_skipped_is_not_failure_and_keeps_output():
    b = Backend()
    b.stage = {"status": "SKIPPED", "reboot_required": True, "output": "CBS requires reboot"}
    result = sp.SpaceCleaner(b).clean({sp.DISM_ID})
    assert not result.failed
    assert "skipped: restart first" in result.text()
    assert "CBS requires reboot" in result.text() and "exit None" not in result.text()


def test_cross_group_deduplication():
    b = Backend()
    b.items["scan_temp_files"] = [ScanItem(r"C:\Temp", 20, True)]
    b.items["scan_user_crash_dumps"] = [ScanItem(r"C:\Temp\crash.dmp", 10, False)]
    result = sp.SpaceCleaner(b).clean({"temp", "dumps"})
    assert [i.path for i in b.deleted] == [r"C:\Temp"]
    assert result.freed == 20


def test_report_preserves_protection_recent_cloud_and_real_denial():
    b = Backend()
    b.items["scan_temp_files"] = [ScanItem(r"C:\Temp", 10, True, keep_newer_than_hours=24)]
    report = DeleteReport(freed=10, files=1, locked=["busy"], denied=["signin", "acl"],
                          protected=[("signin", "sign-in data")], kept_recent=8, cloud_only=3)
    b.delete = lambda *args: (report, [], [(r"C:\Temp", report)])
    result = sp.SpaceCleaner(b).clean({"temp"})
    assert result.denied == 1 and result.protected == [("signin", "sign-in data")]
    assert result.locked_paths == ["busy"] and result.denied_paths == ["acl"]
    text = result.text()
    for part in ("kept 8 recent", "newer than 24 h", "1 in use", "1 protected", "sign-in data", "3 cloud", "acl"):
        assert part in text


def test_combined_measurement_obeys_age_and_cancels(tmp_path, monkeypatch):
    (tmp_path / "nested").mkdir()
    old, recent = tmp_path / "nested" / "old", tmp_path / "new"
    old.write_bytes(b"x" * 8)
    recent.write_bytes(b"y" * 11)
    os.utime(old, (time.time() - 90000, time.time() - 90000))
    monkeypatch.setattr(sp, "_file_size", lambda p: os.lstat(p).st_size)
    backend = sp.CleanupBackend()
    item = ScanItem(str(tmp_path), 999, True, keep_newer_than_hours=24)
    where = backend.measure(item, cancelled=lambda: False)
    assert where.size == 19 and where.eligible == 8
    assert sum(e.eligible for e in where.children) == 8
    with pytest.raises(sp.ScanCancelled):
        backend.measure(item, cancelled=lambda: True)


def test_tree_eligible_totals_empty_and_unreadable(qapp):
    from modules.game_mode import space_tree
    from PyQt6.QtCore import Qt
    tree = space_tree.build_tree(None)
    loc = sp.Location(r"C:\Temp", 100, True, keep_newer_than_hours=24, eligible=0)
    space_tree.fill(tree, {"temp": [loc], "wer": [], "logs": "denied"})
    group = tree.topLevelItem(0)
    group.setCheckState(0, Qt.CheckState.Checked)
    assert space_tree.chosen(tree).total == 0
    assert "all files newer than 24 h" in group.child(0).text(2)
    assert any("Nothing to clean" in tree.topLevelItem(i).text(2) for i in range(tree.topLevelItemCount()))
    assert any("could not check" in tree.topLevelItem(i).text(2) for i in range(tree.topLevelItemCount()))


def test_unreadable_location_cannot_be_selected_through_its_group(qapp):
    from modules.game_mode import space_tree as t
    from PyQt6.QtCore import Qt
    tree = t.build_tree(None)
    t.fill(tree, {"temp": [sp.Location("readable", 10, False, eligible=10),
                           sp.Location("unreadable", 0, True, error="access denied")]})
    tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    assert set(t.chosen(tree).choice["temp"]) == {"readable"}


def test_windows_logs_uses_systemroot_and_cabs_have_30_day_floor(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as s
    root = tmp_path / "Windows" / "Logs" / "CBS"
    root.mkdir(parents=True)
    old, new = root / "CbsPersist_old.cab", root / "CbsPersist_new.cab"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    os.utime(old, (time.time() - 31 * 86400,) * 2)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    result = s.scan_windows_logs()
    assert [i.path for i in result.items] == [str(old)]


def test_real_wu_busy_check_never_queries_or_stops_trustedinstaller(monkeypatch):
    import win32service
    import win32com.client
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *a: pytest.fail("SCM must not be queried"))
    monkeypatch.setattr(win32com.client, "Dispatch", lambda name: SimpleNamespace(IsBusy=False))
    assert sp.CleanupBackend().servicing_busy() == (False, "")


def test_breakdown_walks_each_directory_once_and_cancels_mid_walk(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import _common
    root = tmp_path / "temp"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "old").write_bytes(b"x")
    monkeypatch.setenv("TEMP", str(root))
    monkeypatch.setenv("windir", str(tmp_path / "absent"))
    scans = []
    original = os.scandir
    def counted(path):
        scans.append(str(path))
        return original(path)
    monkeypatch.setattr(os, "scandir", counted)
    b = sp.CleanupBackend()
    items = b.discover("scan_temp_files", 0).items
    assert not scans and _common.directory_sizes_enabled.get()
    b.measure(items[0])
    assert sorted(scans) == sorted([str(root), str(root / "sub")])
    scans.clear()
    with pytest.raises(sp.ScanCancelled):
        b.measure(items[0], cancelled=lambda: len(scans) >= 1)
    assert scans == [str(root)]


def test_breakdown_cancellation_is_propagated_without_other_scans():
    with pytest.raises(sp.ScanCancelled):
        sp.SpaceCleaner(Backend()).breakdown(cancelled=lambda: True)


def test_tree_dedupes_selected_groups_and_partial_children(qapp):
    from modules.game_mode import space_tree as t
    from PyQt6.QtCore import Qt
    tree = t.build_tree(None)
    loc = sp.Location(r"C:\Temp", 30, True, eligible=20,
                      children=[sp.Entry(r"C:\Temp\crash.dmp", 10, False, eligible=10),
                                sp.Entry(r"C:\Temp\other", 20, False, eligible=10)])
    t.fill(tree, {"temp": [loc], "dumps": [sp.Location(r"c:\temp\CRASH.dmp", 10, False, eligible=10)]})
    groups = {tree.topLevelItem(i).data(0, t.ROLE)[1]: tree.topLevelItem(i) for i in range(tree.topLevelItemCount())}
    groups["temp"].child(0).child(0).setCheckState(0, Qt.CheckState.Checked)
    groups["dumps"].setCheckState(0, Qt.CheckState.Checked)
    assert t.chosen(tree).total == 10 and t.chosen(tree).count == 1
    groups["temp"].setCheckState(0, Qt.CheckState.Checked)
    assert t.chosen(tree).total == 20 and t.chosen(tree).count == 1


def test_do_uses_absolute_powershell_and_reports_policy_refusal(monkeypatch):
    from core import system_paths
    calls = []
    monkeypatch.setattr(system_paths, "powershell_exe", lambda: r"D:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="Disabled by policy")
    monkeypatch.setattr(subprocess, "run", run)
    result = sp.SpaceCleaner().clean({"delivery"})
    assert "Disabled by policy" in result.text() and result.failed
    assert calls[0][0].startswith("D:\\Windows\\")
    assert "Delete-DeliveryOptimizationCache -Force" in calls[0][-1]


def test_do_size_comes_from_windows_status(monkeypatch):
    from core import system_paths
    calls = []
    monkeypatch.setattr(system_paths, "powershell_exe", lambda: r"D:\Windows\powershell.exe")
    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="42\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert sp.delivery_size() == 42
    assert "Get-DeliveryOptimizationStatus" in calls[0][-1]
    assert "-Property FileSizeInCache" in calls[0][-1]


def test_do_defaults_ticked_thumbnails_unticked_and_iconcache_excluded(qapp, monkeypatch):
    from modules.cleanup import cleanup_scanner as c
    from modules.game_mode import space_tree as t
    from PyQt6.QtCore import Qt
    monkeypatch.setattr(c, "scan_thumbnail_cache", lambda min_age_days: ScanResult([
        ScanItem(r"C:\Explorer\thumbcache_32.db", 15, False),
        ScanItem(r"C:\Explorer\iconcache_32.db", 30, False)]))
    assert [i.size for i in sp.CleanupBackend().scan("scan_thumbnail_cache", 0).items] == [15]
    tree = t.build_tree(None)
    t.fill(tree, {"delivery": [sp.Location("DO", 50, False, eligible=50)],
                  "thumbnails": [sp.Location("thumbcache_32.db", 15, False, eligible=15)]})
    picked = t.chosen(tree)
    assert set(picked.choice) == {"delivery"} and picked.total == 50


def test_recommendations_use_unelevated_shell(qapp, monkeypatch):
    from core import shell_launch
    from modules.game_mode.optimise_tab import OptimiseTab
    calls = []
    monkeypatch.setattr(shell_launch, "launch", lambda p: calls.append(p))
    tab = OptimiseTab(None, optimiser=SimpleNamespace(space=object()))
    tab._recommendations.click()
    assert calls == ["ms-settings:storagerecommendations"]
    tab.close()


def test_recent_bytes_survive_real_deleter(tmp_path):
    from modules.cleanup.cleanup_scanner.deleter import delete_path
    (tmp_path / "fresh").write_bytes(b"x" * 12)
    report = delete_path(str(tmp_path), True, keep_newer_than_s=86400)
    assert report.kept_recent == 1 and report.kept_recent_bytes == 12
    assert report.freed == 0


def test_real_dism_pending_restart_skips_without_launching(monkeypatch):
    from core import pending_reboot
    monkeypatch.setattr(pending_reboot, "check", lambda: SimpleNamespace(needed=True))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must not launch DISM"))
    result = sp.SpaceCleaner().clean({sp.DISM_ID})
    assert result.dism["status"] == "SKIPPED" and not result.failed


def test_real_failure_banner_survives_refresh_but_restart_skip_has_none(qapp):
    from modules.game_mode.optimise_tab import OptimiseTab
    from core.worker import Worker
    tab = OptimiseTab(None, optimiser=SimpleNamespace(space=object()))
    worker = Worker(lambda w: None)
    tab._worker = worker
    result = sp.CleanResult(dism={"status": "FAILED", "error": "DISM could not launch"})
    tab._done(worker, "space", result)
    assert not tab._banner.isHidden()
    tab._worker = None
    tab._pending = None
    tab._pool = SimpleNamespace(start=lambda w: None)
    tab._start("refresh", lambda w: None)
    assert not tab._banner.isHidden()
    tab._worker = None
    skipped = sp.CleanResult(dism={"status": "SKIPPED", "reboot_required": True})
    tab._banner.hide()
    tab._worker = worker
    tab._done(worker, "space", skipped)
    assert tab._banner.isHidden()
    tab.close()


def test_unreadable_glob_parent_is_not_reported_as_empty(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import _common
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    def denied(path):
        raise PermissionError("cannot list Explorer cache")
    monkeypatch.setattr(os, "scandir", denied)
    with pytest.raises(PermissionError, match="cannot list"):
        sp.CleanupBackend().discover("scan_thumbnail_cache", 0)
    assert _common.directory_sizes_enabled.get()


def test_report_limits_path_lists_to_ten_each():
    b = Backend()
    b.items["scan_temp_files"] = [ScanItem(r"C:\Temp", 0, True)]
    report = DeleteReport(locked=[f"busy-{i}" for i in range(15)],
                          protected=[(f"keep-{i}", "sign-in") for i in range(15)])
    b.delete = lambda *args: (report, [], [(r"C:\Temp", report)])
    text = sp.SpaceCleaner(b).clean({"temp"}).text()
    assert "busy-9" in text and "busy-10" not in text
    assert "keep-9" in text and "keep-10" not in text
