"""SystemHealthModule's skeleton and Findings tab. Servicing tab's own
behavior (DISM buttons, ResetBase gating) is covered in Task 5's own
additions to this same file.
"""
import tempfile

import pytest


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
