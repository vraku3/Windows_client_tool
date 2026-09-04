r"""The three menu-bar pictogram buttons: force shutdown, restart, restart
to Safe Mode.

Nothing here touches a real machine — `subprocess.Popen` and `bcdedit`'s
`CompletedProcess` are faked throughout, `confirm_destructive` is patched so
no dialog blocks a headless run, and Safe Mode's write goes through the
handler directly rather than `.click()`, which a disabled `QToolButton`
would not even deliver.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ui import power_actions as pa


# ── the pure functions ──────────────────────────────────────────────────

def test_force_shutdown_uses_the_force_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(pa.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    pa.force_shutdown()
    assert calls == [["shutdown", "/s", "/f", "/t", "0"]]


def test_restart_does_not_force(monkeypatch):
    """No `/f`: Windows still asks a program with unsaved work to close,
    the same as choosing Restart from the Start menu."""
    calls = []
    monkeypatch.setattr(pa.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    pa.restart()
    assert calls == [["shutdown", "/r", "/t", "0"]]


def test_restart_to_safe_mode_sets_the_flag_then_restarts(monkeypatch):
    monkeypatch.setattr(
        "modules.power_boot.power_module.enable_safe_mode",
        lambda: SimpleNamespace(returncode=0, stdout="", stderr=""))
    calls = []
    monkeypatch.setattr(pa.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    pa.restart_to_safe_mode()
    assert calls == [["shutdown", "/r", "/t", "0"]]


def test_a_refused_bcdedit_write_restarts_nothing(monkeypatch):
    """Restarting on an unset flag would silently boot NORMALLY while the
    confirmation dialog told the user Safe Mode — so nothing restarts."""
    monkeypatch.setattr(
        "modules.power_boot.power_module.enable_safe_mode",
        lambda: SimpleNamespace(returncode=1, stdout="",
                                stderr="Access is denied."))
    calls = []
    monkeypatch.setattr(pa.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    with pytest.raises(RuntimeError, match="Access is denied"):
        pa.restart_to_safe_mode()
    assert calls == []


# ── the widget's gating ─────────────────────────────────────────────────

def _widget(monkeypatch, admin: bool) -> pa.PowerActionsWidget:
    monkeypatch.setattr(pa, "is_admin", lambda: admin)
    return pa.PowerActionsWidget()


def test_three_buttons_in_order(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    layout = widget.layout()
    assert layout.count() == 3
    assert layout.itemAt(0).widget().text() == "⏻"
    assert layout.itemAt(1).widget().text() == "⟲"
    assert layout.itemAt(2).widget().text() == "\U0001f6e1"


def test_safe_mode_is_disabled_and_visibly_dimmed_when_not_admin(monkeypatch):
    """Disabling alone does not dim a colour emoji glyph — measured: Qt's
    disabled palette only recolours the text pen, and a colour emoji is not
    drawn with it, so the button was pixel-identical enabled or not. A
    compositing opacity effect is what actually makes it look disabled."""
    widget = _widget(monkeypatch, admin=False)
    safe_btn = widget.layout().itemAt(2).widget()
    assert safe_btn.isEnabled() is False
    assert "administrator" in safe_btn.toolTip().lower()
    effect = safe_btn.graphicsEffect()
    assert effect is not None
    assert effect.opacity() < 1.0


def test_safe_mode_is_enabled_and_undimmed_when_admin(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    safe_btn = widget.layout().itemAt(2).widget()
    assert safe_btn.isEnabled() is True
    assert safe_btn.graphicsEffect() is None


# ── the click handlers ──────────────────────────────────────────────────

def test_shutdown_click_does_nothing_without_confirmation(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    monkeypatch.setattr(pa, "confirm_destructive", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(pa, "force_shutdown", lambda: calls.append(1))
    widget.layout().itemAt(0).widget().click()
    assert calls == []


def test_shutdown_click_shuts_down_when_confirmed(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    monkeypatch.setattr(pa, "confirm_destructive", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(pa, "force_shutdown", lambda: calls.append(1))
    widget.layout().itemAt(0).widget().click()
    assert calls == [1]


def test_restart_click_restarts_when_confirmed(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    monkeypatch.setattr(pa, "confirm_destructive", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(pa, "restart", lambda: calls.append(1))
    widget.layout().itemAt(1).widget().click()
    assert calls == [1]


def test_the_safe_mode_handler_refuses_outright_when_not_admin(monkeypatch):
    """Belt and braces on top of the disabled button Qt already refuses to
    click: `_do_safe_mode` itself checks `is_admin()` first."""
    widget = _widget(monkeypatch, admin=False)
    calls = []
    monkeypatch.setattr(pa, "restart_to_safe_mode", lambda: calls.append(1))
    widget._do_safe_mode()
    assert calls == []


def test_safe_mode_click_restarts_when_admin_and_confirmed(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    monkeypatch.setattr(pa, "confirm_destructive", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(pa, "restart_to_safe_mode", lambda: calls.append(1))
    widget._do_safe_mode()
    assert calls == [1]


def test_a_failed_safe_mode_write_is_reported_not_swallowed(monkeypatch):
    widget = _widget(monkeypatch, admin=True)
    monkeypatch.setattr(pa, "confirm_destructive", lambda *a, **k: True)

    def _boom():
        raise RuntimeError("bcdedit would not set the Safe Mode boot flag: "
                          "Access is denied.")

    monkeypatch.setattr(pa, "restart_to_safe_mode", _boom)
    warned = []
    monkeypatch.setattr(pa.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))

    widget._do_safe_mode()  # must not raise -- the exception is caught

    assert warned
    assert "Access is denied" in warned[0][-1]
