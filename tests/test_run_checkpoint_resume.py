"""Tests for run checkpoint stage progress, remaining stages, and smart resume."""
from __future__ import annotations

from types import SimpleNamespace
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox, QPushButton

from modules.updates import run_checkpoint as rc
from modules.updates.run_all_tab import _RunAllTab


def test_stage_start_and_complete(tmp_path):
    rc.begin(tmp_path, ["wu", "winget", "cleanup"], "run_all")
    assert rc.remaining_stages(rc.load(tmp_path)) == ["wu", "winget", "cleanup"]

    rc.stage_start(tmp_path, "wu")
    data = rc.load(tmp_path)
    assert data["current_stage"] == "wu"

    rc.stage_complete(tmp_path, "wu")
    data = rc.load(tmp_path)
    assert data["completed_stages"] == ["wu"]
    assert rc.remaining_stages(data) == ["winget", "cleanup"]


def test_reconcile_checkpoint_with_installed_hotfixes(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    rc.begin(tmp_path, ["wu", "winget"], "run_all")
    rc.item(tmp_path, "wu:KB5132061", "started", "Security Update")

    # Simulate reboot: KB5132061 is now present on OS
    reconciled = rc.reconcile_checkpoint(tmp_path, installed_kbs={"KB5132061"})
    assert reconciled is not None
    assert reconciled["items"]["wu:KB5132061"]["phase"] == "installed_after_reboot"
    assert "wu" in reconciled["completed_stages"]
    assert rc.remaining_stages(reconciled) == ["winget"]


def test_reconcile_checkpoint_finishes_if_no_remaining_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    rc.begin(tmp_path, ["wu"], "run_all")
    rc.item(tmp_path, "wu:KB5132061", "started", "Security Update")

    # Reconcile when only WU was planned
    reconciled = rc.reconcile_checkpoint(tmp_path, installed_kbs={"KB5132061"})
    assert reconciled is not None
    assert reconciled["status"] == "completed_after_reboot"
    assert rc.unfinished(tmp_path) is None  # no longer unfinished!


def test_run_all_tab_resume_button_checks_remaining_stages(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    rc.begin(tmp_path, ["wu", "winget", "store"], "run_all")
    rc.stage_complete(tmp_path, "wu")

    tab = _RunAllTab()
    tab.set_app(SimpleNamespace(app_data_dir=str(tmp_path)))
    tab.refresh_checkpoint_note()

    assert not tab._checkpoint_bar.isHidden()
    assert not tab._checkpoint_resume_btn.isHidden()
    assert "Resume 2 stages" in tab._checkpoint_resume_btn.text()

    # Monkeypatch _do_run to verify stage checkboxes
    run_called = []
    monkeypatch.setattr(tab, "_do_run", lambda: run_called.append(True))

    tab._checkpoint_resume_btn.click()
    assert run_called == [True]
    assert not tab._chk_wu.isChecked()
    assert tab._chk_winget.isChecked()
    assert tab._chk_store.isChecked()
    assert rc.load(tmp_path) is None  # cleared for the new run


def test_confirm_preflight_offers_resume_button(qapp, tmp_path, monkeypatch):
    from modules.updates import reboot_preflight

    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    rc.begin(tmp_path, ["wu", "winget"], "run_all")
    rc.stage_complete(tmp_path, "wu")
    monkeypatch.setattr(reboot_preflight, "_read_reboot", lambda: ([], False))

    tab = _RunAllTab()
    tab.set_app(SimpleNamespace(app_data_dir=str(tmp_path)))

    resume_clicked = []
    monkeypatch.setattr(tab, "_on_resume_checkpoint", lambda: resume_clicked.append(True))

    def on_dialog():
        box = None
        for w in qapp.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                box = w
                break
        assert box is not None
        resume_btn = None
        for btn in box.findChildren(QPushButton):
            if "Resume remaining" in (btn.text() or ""):
                resume_btn = btn
                break
        assert resume_btn is not None
        resume_btn.click()

    QTimer.singleShot(50, on_dialog)
    res = tab._confirm_preflight()
    assert res is False  # _on_resume_checkpoint handles the run directly
    assert resume_clicked == [True]
