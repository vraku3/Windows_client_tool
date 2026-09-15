"""SystemHealthModule's skeleton and Findings tab. Servicing tab's own
behavior (DISM buttons, ResetBase gating) is covered in Task 5's own
additions to this same file.
"""
import tempfile

import pytest
from PyQt6.QtWidgets import QMessageBox


def _module(qapp):
    from app import App
    from modules.system_health.system_health_module import SystemHealthModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = SystemHealthModule()
    module.on_start(app)
    widget = module.create_widget()
    module._keep_alive = widget
    return module, app


def test_module_declares_the_right_admin_model():
    from modules.system_health.system_health_module import SystemHealthModule
    assert SystemHealthModule.requires_admin is True
    assert SystemHealthModule.read_only_unelevated is True


def test_create_widget_builds_two_tabs(qapp):
    module, app = _module(qapp)
    try:
        assert module._tabs.count() == 2
        assert module._tabs.tabText(0) == "Findings"
        assert module._tabs.tabText(1) == "Servicing"
    finally:
        app.shutdown()


def test_refresh_findings_populates_the_list(qapp, monkeypatch):
    from modules.system_health.findings import Finding

    module, app = _module(qapp)
    try:
        fake_findings = [Finding(id="x", title="Test Finding", detail="detail text", severity="info")]
        monkeypatch.setattr("modules.system_health.findings.all_findings", lambda: fake_findings)

        module._refresh_findings()
        # Worker runs on a thread pool -- settle it.
        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert module._findings_list.count() == 1
        assert "Test Finding" in module._findings_list.item(0).text()
    finally:
        app.shutdown()


def test_on_activate_triggers_a_findings_refresh(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        calls = []
        monkeypatch.setattr(module, "_refresh_findings", lambda: calls.append(1))
        module.on_activate()
        assert calls == [1]
    finally:
        app.shutdown()


def test_servicing_tab_has_three_buttons(qapp):
    module, app = _module(qapp)
    try:
        assert hasattr(module, "_scan_health_btn")
        assert hasattr(module, "_component_cleanup_btn")
        assert hasattr(module, "_reset_base_btn")
    finally:
        app.shutdown()


def test_reset_base_is_disabled_until_a_clean_scan_health(qapp):
    module, app = _module(qapp)
    try:
        assert module._reset_base_btn.isEnabled() is False
    finally:
        app.shutdown()


def test_scan_health_success_enables_reset_base(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(
            "modules.system_health.servicing.run_scan_health",
            lambda: DismResult("dism /Online /Cleanup-Image /ScanHealth", 0, "No corruption"))
        module._run_scan_health()

        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert module._last_scan_health_clean is True
        assert module._reset_base_btn.isEnabled() is True
    finally:
        app.shutdown()


def test_scan_health_failure_does_not_enable_reset_base(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(
            "modules.system_health.servicing.run_scan_health",
            lambda: DismResult("dism /Online /Cleanup-Image /ScanHealth", 87, "Corruption found"))
        module._run_scan_health()

        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert module._last_scan_health_clean is False
        assert module._reset_base_btn.isEnabled() is False
    finally:
        app.shutdown()


def test_reset_base_click_requires_typed_confirmation(qapp, monkeypatch):
    """The confirmation dialog's Ok button must stay disabled until the
    exact word RESETBASE is typed -- simulate this by driving the real
    dialog class directly rather than mocking QMessageBox.exec, since
    this needs a QDialog with a QLineEdit, not a plain QMessageBox."""
    from modules.system_health.system_health_module import _ResetBaseConfirmDialog

    dlg = _ResetBaseConfirmDialog()
    ok_button = dlg._ok_button
    assert ok_button.isEnabled() is False

    dlg._confirm_field.setText("wrong")
    assert ok_button.isEnabled() is False

    dlg._confirm_field.setText("RESETBASE")
    assert ok_button.isEnabled() is True


def test_reset_base_creates_a_restore_point_before_running_dism(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        module._last_scan_health_clean = True
        module._reset_base_btn.setEnabled(True)

        restore_calls = []
        monkeypatch.setattr(
            "core.system_restore.create_restore_point",
            lambda description, timeout=60: (restore_calls.append(description), (True, "ok"))[1])
        dism_calls = []
        monkeypatch.setattr(
            "modules.system_health.servicing.run_reset_base",
            lambda: (dism_calls.append(1), DismResult("dism ... /ResetBase", 0, "done"))[1])
        # Skip the interactive typed-confirmation dialog for this test --
        # call the internal method the dialog's Ok button would trigger.
        module._do_reset_base_confirmed()

        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(restore_calls) == 1
        assert len(dism_calls) == 1
    finally:
        app.shutdown()


def _settle(qapp):
    from PyQt6.QtCore import QThreadPool
    import time
    QThreadPool.globalInstance().waitForDone(5000)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_sfc_scan_cancelled_confirmation_does_not_run(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
        monkeypatch.setattr(
            "modules.system_health.servicing.run_sfc_scan",
            lambda: (_ for _ in ()).throw(AssertionError("must not run when cancelled")))

        module._run_sfc_scan()

        assert module._sfc_btn.isEnabled() is True
    finally:
        app.shutdown()


def test_sfc_scan_confirmed_disables_button_and_logs_history(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(
            "modules.system_health.servicing.run_sfc_scan",
            lambda: DismResult("sfc /scannow", 0, "No integrity violations found."))
        history_calls = []
        monkeypatch.setattr(
            "modules.system_health.history.append_run",
            lambda app_data_dir, **kw: history_calls.append(kw))

        module._run_sfc_scan()
        assert module._sfc_btn.isEnabled() is False, "button should disable while the scan runs"

        _settle(qapp)

        assert module._sfc_btn.isEnabled() is True
        assert len(history_calls) == 1
        assert history_calls[0]["action"] == "sfc_scan"
    finally:
        app.shutdown()


def test_restore_health_cancelled_confirmation_does_not_run(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
        monkeypatch.setattr(
            "modules.system_health.servicing.run_restore_health",
            lambda: (_ for _ in ()).throw(AssertionError("must not run when cancelled")))

        module._run_restore_health()

        assert module._restore_health_btn.isEnabled() is True
    finally:
        app.shutdown()


def test_restore_health_confirmed_disables_button_and_logs_history(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(
            "modules.system_health.servicing.run_restore_health",
            lambda: DismResult("dism /online /cleanup-image /restorehealth", 0, "Repaired."))
        history_calls = []
        monkeypatch.setattr(
            "modules.system_health.history.append_run",
            lambda app_data_dir, **kw: history_calls.append(kw))

        module._run_restore_health()
        assert module._restore_health_btn.isEnabled() is False, "button should disable while running"

        _settle(qapp)

        assert module._restore_health_btn.isEnabled() is True
        assert len(history_calls) == 1
        assert history_calls[0]["action"] == "restore_health"
    finally:
        app.shutdown()


def test_chkdsk_schedule_cancelled_confirmation_does_not_run(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
        monkeypatch.setattr(
            "modules.system_health.servicing.run_chkdsk_schedule",
            lambda: (_ for _ in ()).throw(AssertionError("must not run when cancelled")))

        module._run_chkdsk_schedule()

        assert module._chkdsk_btn.isEnabled() is True
    finally:
        app.shutdown()


def test_chkdsk_schedule_confirmed_disables_button_and_logs_history(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
        monkeypatch.setattr(
            "modules.system_health.servicing.run_chkdsk_schedule",
            lambda: DismResult("chkdsk C: /f /r /x", 0, "Scheduled."))
        history_calls = []
        monkeypatch.setattr(
            "modules.system_health.history.append_run",
            lambda app_data_dir, **kw: history_calls.append(kw))

        module._run_chkdsk_schedule()
        assert module._chkdsk_btn.isEnabled() is False, "button should disable while scheduling runs"

        _settle(qapp)

        assert module._chkdsk_btn.isEnabled() is True
        assert len(history_calls) == 1
        assert history_calls[0]["action"] == "chkdsk_schedule"
    finally:
        app.shutdown()
