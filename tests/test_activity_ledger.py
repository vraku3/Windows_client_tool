import json
import pytest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from modules.updates import activity_ledger as ledger


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def entry(package="Vendor.App", **values):
    return {"id": package, "name": package, "source": "winget", "outcome": "UPDATED",
            "context": "manual", **values}


def test_persists_newest_first_and_filters_before_limiting(tmp_path):
    ledger.record_activity(tmp_path, entry(version_after="2"))
    ledger.record_activity(tmp_path, entry("Other.App"))
    ledger.record_activity(tmp_path, entry(version_after="3"))
    assert ledger.load_activity(tmp_path, "vendor.app", limit=1)[0]["version_after"] == "3"
    assert len(ledger.package_history(tmp_path, "Vendor.App")) == 2
    assert ledger.load_activity(tmp_path, limit=0) == []
    saved = json.loads((tmp_path / "updates/activity.jsonl").read_text().splitlines()[0])
    assert saved["version_before"] is None
    assert isinstance(saved["elevated"], bool)
    assert datetime.fromisoformat(saved["ts"])


def test_corrupt_lines_and_non_objects_are_skipped(tmp_path):
    ledger.record_activity(tmp_path, entry())
    with (tmp_path / "updates/activity.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('broken\n[]\nnull\n{"id": 42}\n')
    assert len(ledger.load_activity(tmp_path)) == 1


def test_trim_has_hysteresis(tmp_path):
    path = tmp_path / "activity.jsonl"
    path.write_text("{}\n" * 2100)
    ledger._trim_if_needed(path)
    assert len(path.read_text().splitlines()) == 2100
    with path.open("a") as stream:
        stream.write('{"last": true}\n')
    ledger._trim_if_needed(path)
    assert len(path.read_text().splitlines()) == 2000
    assert json.loads(path.read_text().splitlines()[-1]) == {"last": True}


def test_recent_includes_all_recent_entries_and_handles_timezones(tmp_path):
    for i in range(105):
        ledger.record_activity(tmp_path, entry(str(i)))
    ledger.record_activity(tmp_path, entry("old", ts=(datetime.now() - timedelta(days=40)).isoformat()))
    ledger.record_activity(tmp_path, entry("bad", ts="not a date"))
    ledger.record_activity(tmp_path, entry("aware", ts=datetime.now(timezone.utc).isoformat()))
    assert len(ledger.recent_activity(tmp_path)) == 106


def test_append_after_truncated_record_preserves_new_attempt(tmp_path):
    ledger.record_activity(tmp_path, entry("first"))
    with (tmp_path / "updates/activity.jsonl").open("a") as stream:
        stream.write('{"id": "truncated')
    ledger.record_activity(tmp_path, entry("last"))
    assert [row["id"] for row in ledger.load_activity(tmp_path)] == ["last", "first"]


def test_concurrent_appends_do_not_lose_or_corrupt_entries(tmp_path):
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: ledger.record_activity(tmp_path, entry(str(i))), range(150)))
    assert len(ledger.load_activity(tmp_path, limit=None)) == 150


def test_unreadable_ledger_does_not_prevent_update_result(tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(ledger, "record_activity", lambda *a: (_ for _ in ()).throw(OSError("full")))
    from types import SimpleNamespace
    app = SimpleNamespace(app_data_dir=tmp_path)
    update = SimpleNamespace(winget_id="A", name="App", source="winget", installed_version="1", available_version="2")
    outcome = SimpleNamespace(state="UPDATED", detail="Now 2", version_after="2")
    ledger.record_winget_activity(app, update, outcome, "manual", messages.append)
    assert "activity" in messages[0].lower()


def test_winget_stage_persists_actual_versions_and_context(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from modules.updates import winget_updater as wg, stage_runners
    app = SimpleNamespace(app_data_dir=tmp_path, config=SimpleNamespace(get=lambda k, d=None: d))
    offer = wg.AppUpdate("App", "Vendor.App", "1", "3", "msstore")
    monkeypatch.setattr(wg, "fetch_updates", lambda **k: [offer])
    monkeypatch.setattr(wg, "install_update", lambda *a, **k: wg.UpdateOutcome("Vendor.App", "UPDATED", "Now 2", "2"))
    stage_runners.run_winget_stage(app, lambda s: None, lambda: False, context="unattended")
    saved = ledger.load_activity(tmp_path)[0]
    assert (saved["version_before"], saved["version_offered"], saved["version_after"]) == ("1", "3", "2")
    assert (saved["source"], saved["context"]) == ("store", "unattended")


def test_wu_result_callback_preserves_completed_results_on_cancellation(monkeypatch):
    import win32com.client
    from types import SimpleNamespace
    from modules.updates.windows_updater import WindowsUpdate, install_updates_iter
    monkeypatch.setattr(win32com.client, "Dispatch", lambda name: SimpleNamespace(
        CreateUpdateDownloader=lambda: None, CreateUpdateInstaller=lambda: None))
    results = []
    offers = [WindowsUpdate("KB1", "First", "", 0, ""), WindowsUpdate("KB2", "Second", "", 0, "")]
    returned = install_updates_iter(offers, result_cb=results.append, is_cancelled=lambda: bool(results))
    assert len(results) == len(returned) == 1
    assert results[0].kb == "KB1"


def test_wu_stage_persists_results_including_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from modules.updates import windows_updater as wu, stage_runners
    app = SimpleNamespace(app_data_dir=tmp_path, config=SimpleNamespace(get=lambda k, d=None: False if k == "updates.restore_point_before_install" else d))
    offers = [wu.WindowsUpdate("KB1", "Patch", "", 0, "")]
    monkeypatch.setattr(wu, "fetch_pending_updates", lambda **k: offers)
    monkeypatch.setattr("core.disk_space.check_wu_preflight", lambda *a: None)
    monkeypatch.setattr("core.windows_utils.is_reboot_pending", lambda: False)
    def install(updates, **kwargs):
        result = wu.InstallResult("KB1", "Patch", False, 0, "Restart to finish", True, 2)
        kwargs["result_cb"](result)
        return [result]
    monkeypatch.setattr(wu, "install_updates_iter", install)
    stage_runners.run_wu_stage(app, lambda s: None, lambda: False)
    saved = ledger.load_activity(tmp_path)[0]
    assert saved["source"] == "wu" and saved["context"] == "run_all"
    assert saved["outcome"] == "UPDATED_UNVERIFIED"
    assert saved["version_after"] is None


def test_manual_winget_persists_inside_worker(tmp_path, monkeypatch, qapp):
    from types import SimpleNamespace
    from modules.updates import updates_module as ui
    monkeypatch.setattr(ui.QThreadPool, "globalInstance", lambda: SimpleNamespace(start=lambda worker: worker.run()))
    tab = ui._AppUpdatesTab()
    tab.set_app(SimpleNamespace(app_data_dir=tmp_path, config=SimpleNamespace(get=lambda k, d=None: d)))
    tab._updates = [ui.AppUpdate("App", "Vendor.App", "1", "2", "winget")]
    monkeypatch.setattr(tab, "_on_update_done", lambda results: None)
    monkeypatch.setattr(ui, "install_update", lambda *a, **k: ui.UpdateOutcome("Vendor.App", "FAILED", "Installer refused"))
    tab._run_updates(["Vendor.App"])
    saved = ledger.load_activity(tmp_path)[0]
    assert saved["context"] == "manual" and saved["outcome"] == "FAILED"
    assert saved["version_after"] is None and saved["version_before"] == "1"
    tab.deleteLater()


def test_manual_wu_persists_inside_worker(tmp_path, monkeypatch, qapp):
    from types import SimpleNamespace
    from modules.updates import updates_module as ui
    monkeypatch.setattr(ui.QThreadPool, "globalInstance", lambda: SimpleNamespace(start=lambda worker: worker.run()))
    monkeypatch.setattr("core.disk_space.check_wu_preflight", lambda *a: None)
    tab = ui._WinUpdatesTab()
    tab.set_app(SimpleNamespace(app_data_dir=tmp_path, config=SimpleNamespace(get=lambda k, d=None: False)))
    tab._updates = [ui.WindowsUpdate("KB1", "Patch", "", 0, "")]
    tab._render_table()
    tab._table.selectRow(0)
    monkeypatch.setattr(tab, "_on_install_done", lambda results: None)
    def install(updates, **kwargs):
        result = ui.InstallResult("KB1", "Patch", True, 0, "Success", False, 2)
        kwargs["result_cb"](result)
        return [result]
    monkeypatch.setattr(ui, "install_updates_iter", install)
    tab._do_install()
    saved = ledger.load_activity(tmp_path)[0]
    assert saved["context"] == "manual" and saved["outcome"] == "UPDATED"
    tab.deleteLater()


def test_winget_exception_is_persisted_before_stage_reports_error(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from modules.updates import winget_updater as wg, stage_runners
    app = SimpleNamespace(app_data_dir=tmp_path, config=SimpleNamespace(get=lambda k, d=None: d))
    monkeypatch.setattr(wg, "fetch_updates", lambda **k: [wg.AppUpdate("App", "A", "1", "2", "winget")])
    def fail(*args, **kwargs):
        raise RuntimeError("read-back failed")
    monkeypatch.setattr(wg, "install_update", fail)
    with pytest.raises(RuntimeError, match="read-back failed"):
        stage_runners.run_winget_stage(app, lambda s: None, lambda: False)
    saved = ledger.load_activity(tmp_path)[0]
    assert saved["outcome"] == "FAILED" and saved["detail"] == "read-back failed"


def test_install_outcome_retains_the_version_read_back(monkeypatch):
    from modules.updates import winget_updater as wg
    monkeypatch.setattr(wg.winget_list, "portable_snapshot", lambda *a: [])
    def run(args, **kwargs):
        kwargs["returncode_out"].append(0)
        if args[0] == "upgrade":
            return "Successfully installed"
        return "Name   Id         Version  Available Source\n------------------------------------------\nApp    Acme.App   2.0                winget\n"
    monkeypatch.setattr(wg, "_run_winget", run)
    result = wg.install_update("Acme.App", lambda s: None, target_version="3.0", before_version="1.0")
    assert result.version_after == "2.0" and result.state == "UPDATED"


def test_independent_processes_share_a_valid_qt_free_ledger(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from modules.updates.activity_ledger import record_activity
assert not any(name.startswith('PyQt6') for name in sys.modules)
for i in range(60):
    record_activity(sys.argv[2], {'id': sys.argv[3] + str(i), 'outcome': 'UPDATED'})
"""
    processes = [subprocess.Popen([sys.executable, "-c", script,
                  str(Path(__file__).resolve().parents[1] / "src"), str(tmp_path), prefix])
                 for prefix in ("A", "B")]
    for process in processes:
        assert process.wait(timeout=30) == 0
    assert len({row["id"] for row in ledger.load_activity(tmp_path, limit=None)}) == 120
