"""The Large Items Windows Disk Cleanup panel (cleanmgr /sageset +
/sagerun). Never runs a real cleanmgr subprocess in a test -- disk_cleanup_
sageset.sageset_command()/sagerun_command() and configured_categories()
are the only things monkeypatched.
"""
import time

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox

from modules.cleanup import disk_cleanup_sageset as dcs


def _settle(qapp):
    QThreadPool.globalInstance().waitForDone(60_000)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def panel(qapp, monkeypatch):
    # The panel refreshes its status off a real background Worker as soon
    # as it's constructed -- point it at a harmless stub so the fixture
    # itself never touches the real registry from inside a Worker thread
    # racing test teardown.
    monkeypatch.setattr(dcs, "configured_categories", lambda: [])
    from modules.cleanup.tabs._disk_cleanup_panel import _DiskCleanupPanel
    p = _DiskCleanupPanel()
    _settle(qapp)
    return p


def test_the_panel_starts_with_run_disabled_when_not_configured(panel):
    assert panel._run_btn.isEnabled() is False
    assert "not configured" in panel._status_lbl.text().lower()


def test_a_refused_status_read_says_so_and_disables_run(qapp, monkeypatch):
    monkeypatch.setattr(dcs, "configured_categories", lambda: None)
    from modules.cleanup.tabs._disk_cleanup_panel import _DiskCleanupPanel
    panel = _DiskCleanupPanel()
    _settle(qapp)
    assert panel._run_btn.isEnabled() is False
    assert "could not" in panel._status_lbl.text().lower()


def test_configured_categories_enable_run(qapp, monkeypatch):
    monkeypatch.setattr(dcs, "configured_categories",
                        lambda: [dcs.ConfiguredCategory("Temporary Files")])
    from modules.cleanup.tabs._disk_cleanup_panel import _DiskCleanupPanel
    panel = _DiskCleanupPanel()
    _settle(qapp)
    assert panel._run_btn.isEnabled() is True
    assert "1 categor" in panel._status_lbl.text().lower()


def test_configure_button_runs_off_the_ui_thread_and_refreshes_status(
    qapp, panel, monkeypatch,
):
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)

    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(dcs, "configured_categories",
                        lambda: [dcs.ConfiguredCategory("Temporary Files")])

    panel._configure()
    assert panel._configure_btn.isEnabled() is False, "button stayed live"
    _settle(qapp)

    assert calls == [dcs.sageset_command()]
    assert panel._configure_btn.isEnabled() is True
    assert panel._run_btn.isEnabled() is True, "status was not refreshed after Configure"


def test_run_asks_for_confirmation_before_calling_sagerun(
    qapp, panel, monkeypatch,
):
    monkeypatch.setattr(dcs, "configured_categories",
                        lambda: [dcs.ConfiguredCategory("Temporary Files")])
    panel._refresh_status()
    _settle(qapp)

    calls = []
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda self: (calls.append("shown"), QMessageBox.StandardButton.Cancel)[1])

    import subprocess
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a) or _FakeCompletedProcess())

    panel._run()

    assert calls == ["shown"]
    assert ran == [], "sagerun ran without the confirmation being accepted"


def test_run_calls_sagerun_after_confirmation(qapp, panel, monkeypatch):
    monkeypatch.setattr(dcs, "configured_categories",
                        lambda: [dcs.ConfiguredCategory("Temporary Files")])
    panel._refresh_status()
    _settle(qapp)

    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.Ok)

    import subprocess
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    panel._run()
    _settle(qapp)

    assert calls == [dcs.sagerun_command()]
    assert "exit 0" in panel._output.toPlainText().lower()


class _FakeCompletedProcess:
    returncode = 0
    stdout = ""
    stderr = ""
