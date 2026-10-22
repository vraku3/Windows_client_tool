"""Focused regressions for the verified Updates review; no real maintenance."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from modules.updates import windows_updater as wu, stage_runners as stages
from test_windows_updater import _Update, _Coll


@pytest.mark.parametrize("code,word", [(2, "installed -- restart"),
                                      (3, "installed with errors -- restart"),
                                      (4, "failed"), (5, "aborted")])
def test_install_result_keeps_error_and_restart(monkeypatch, code, word):
    import win32com.client
    identity = _Update()
    identity.IsDownloaded = identity.EulaAccepted = True
    result = NS(ResultCode=code, HResult=-2145124329, RebootRequired=True)
    session = NS(CreateUpdateDownloader=lambda: NS(),
                 CreateUpdateInstaller=lambda: NS(Install=lambda: result))
    monkeypatch.setattr(win32com.client, "Dispatch", lambda name:
                        NS(Add=lambda u: None) if name.endswith("UpdateColl") else session)
    installed = wu.install_updates_iter([wu.WindowsUpdate("KB1", "T", "c", 1, "d", identity)])[0]
    assert word in installed.message.lower()
    assert installed.result_code == code and installed.reboot_required
    assert not installed.success
    failures = stages.stage_failures("wu", {"results": [vars(installed)]})
    assert bool(failures) == (code != 2)
    if code != 2:
        assert "0x80240017" in installed.message
        assert "restart" in installed.message


@pytest.mark.parametrize("operation,same_id,fresh,needed,expected", [
    (1, True, True, True, True), (2, True, True, True, False),
    (1, False, True, True, False), (1, True, False, True, False),
    (1, True, True, False, False), (None, True, True, True, False)])
def test_pending_history_requires_install_identity_current_boot(monkeypatch, operation, same_id,
                                                               fresh, needed, expected):
    import win32com.client
    from core import pending_reboot
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=needed, reasons=["KB5001234"]))
    identity = _Update()
    identity.Identity = NS(UpdateID="offer")
    entry = NS(Title=identity.Title, Operation=operation, ResultCode=1,
               UpdateIdentity=NS(UpdateID="offer" if same_id else "other"),
               Date=datetime.now(timezone.utc) if fresh else datetime(2000, 1, 1, tzinfo=timezone.utc))
    searcher = NS(Search=lambda _: NS(Updates=_Coll([identity])),
                  GetTotalHistoryCount=lambda: 1, QueryHistory=lambda *a: _Coll([entry]))
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: NS(CreateUpdateSearcher=lambda: searcher))
    assert wu.fetch_pending_updates()[0].pending_restart is expected


def test_verified_scan_keeps_missing_restart_row():
    from modules.updates.updates_module import _WinUpdatesTab
    old = wu.WindowsUpdate("KB1", "Installed", "Security", 1, "d")
    new = wu.WindowsUpdate("KB2", "New offer", "Security", 2, "d")
    tab = _WinUpdatesTab()
    try:
        tab._updates = [old]
        tab._on_verify_done([wu.InstallResult("KB1", "Installed", False, 0, "restart", True)],
                            {"updates": [new], "held": []})
        assert len(tab._updates) == 2
        assert sum(u.pending_restart for u in tab._updates) == 1
        assert "1 pending update(s)" in tab._status_lbl.text()
        assert "1 installed -- restart to finish" in tab._status_lbl.text()
        assert any(tab._table.item(r, 7).text() == wu.RESTART_TEXT for r in range(2))
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("downloaded,minimum,expected,unknown", [
    (True, None, 0, False), (False, 20, 20, False),
    (False, 0, 0, False), (False, None, 0, True)])
def test_download_preflight_does_not_use_huge_max(downloaded, minimum, expected, unknown):
    identity = NS(IsDownloaded=downloaded)
    if minimum is not None:
        identity.MinDownloadSize = minimum * 1024 * 1024
    update = wu.WindowsUpdate("KB1", "T", "c", 92638, "d", identity)
    warnings = []
    assert wu.download_size_mb([update], warnings.append) == expected
    assert any("size unknown" in line for line in warnings) is unknown


def test_incomplete_notification_includes_stage_failure_and_totals():
    from modules.updates.unattended_runner import completion_message
    text = completion_message({"wu_installed": 2}, "r.html", False, ["dism: failed"])
    assert "Maintenance INCOMPLETE: 1 problem(s) (first: dism: failed)" in text
    assert "2 updates installed" in text


def test_notification_uses_shared_absolute_powershell(tmp_path, monkeypatch):
    from modules.updates import maintenance_notify
    from core import system_paths
    from modules.game_mode.engine import optimise
    calls = []
    monkeypatch.setattr(maintenance_notify.subprocess, "Popen", lambda args, **k: calls.append(args))
    maintenance_notify.notify_finished("done", str(tmp_path))
    assert calls[0][0] == system_paths.powershell_exe()
    assert optimise.powershell_exe is system_paths.powershell_exe
    import pyinstaller_common
    assert "core.system_paths" in pyinstaller_common.HIDDEN_IMPORTS


@pytest.mark.parametrize("path", ["stage", "ui"])
def test_install_paths_use_download_estimate(monkeypatch, path):
    from modules.updates import updates_module as ui
    sizes = []
    monkeypatch.setattr("core.disk_space.check_wu_preflight", lambda size: sizes.append(size) or None)
    update = wu.WindowsUpdate("KB1", "T", "c", 92638, "d", downloaded=True)
    if path == "stage":
        monkeypatch.setattr(wu, "fetch_pending_updates", lambda **k: [update])
        monkeypatch.setattr(wu, "install_updates_iter", lambda *a, **k: [])
        monkeypatch.setattr("core.windows_utils.is_reboot_pending", lambda: False)
        app = NS(config=NS(get=lambda key, default: False if "restore_point" in key else default))
        stages.run_wu_stage(app, lambda _: None, lambda: False)
    else:
        # Capture the queued operation, without running an installer or restore point.
        monkeypatch.setattr(ui.QThreadPool, "globalInstance", lambda: NS(start=lambda worker: None))
        tab = ui._WinUpdatesTab()
        try:
            tab._updates = [update]
            tab._render_table()
            tab._table.selectRow(0)
            tab._do_install()
        finally:
            tab.deleteLater()
    assert sizes == [0]


@pytest.mark.parametrize("raises", [False, True])
def test_unattended_stage_failure_drives_exit_and_notification(tmp_path, monkeypatch, raises):
    from app import App
    from core import pending_reboot
    from modules.updates import unattended_runner, maintenance_notify, history_writer, report_generator
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(App, "instance", NS(app_data_dir=str(tmp_path), shutdown=lambda: None))
    def run(*args):
        if raises:
            raise RuntimeError("stage exploded")
        return {"error": "stage refused"}
    monkeypatch.setitem(stages.STAGE_RUNNERS, "dism", run)
    monkeypatch.setattr(history_writer, "append_run", lambda *a, **k: None)
    monkeypatch.setattr(history_writer, "load_history", lambda *a: [])
    monkeypatch.setattr(report_generator, "write_report", lambda *a: "r.html")
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=False))
    messages = []
    monkeypatch.setattr(maintenance_notify, "notify_finished", lambda text, path: messages.append(text))
    assert unattended_runner.run_unattended(["dism"]) == 1
    assert "INCOMPLETE: 1 problem(s) (first: dism: stage " in messages[0]
    assert ("exploded" if raises else "refused") in messages[0]


def test_isinstalled_alone_is_not_restart_evidence():
    assert not wu._awaiting_restart(NS(IsInstalled=True, RebootRequired=False), set())


@pytest.mark.parametrize("code", [3, 4, 5])
def test_verified_failed_restart_result_stays_failure(code):
    from modules.updates.updates_module import _WinUpdatesTab
    tab = _WinUpdatesTab()
    try:
        offered = wu.WindowsUpdate("KB1", "T", "c", 1, "d")
        result = wu.InstallResult("KB1", "T", False, 1, "error; restart required", True, code)
        tab._on_verify_done([result], {"updates": [offered], "held": []})
        assert offered.pending_restart is (code == 3)
        assert tab._table.item(0, 7).text().startswith("FAILED")
    finally:
        tab.deleteLater()


def test_partial_install_restart_row_survives_missing_offer_without_hiding_error():
    from modules.updates.updates_module import _WinUpdatesTab
    tab = _WinUpdatesTab()
    try:
        result = wu.InstallResult("KB1", "T", False, -2145124329,
                                  "installed with errors -- restart to finish; 0x80240017", True, 3)
        tab._on_verify_done([result], {"updates": [], "held": []})
        assert len(tab._updates) == 1
        text = tab._table.item(0, 7).text()
        assert "errors" in text and "0x80240017" in text and "restart to finish" in text
    finally:
        tab.deleteLater()
