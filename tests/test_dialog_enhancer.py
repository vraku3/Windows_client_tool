"""Tests for global dialog enhancer and copy-to-clipboard functionality."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from ui.dialog_enhancer import extract_box_text, install_dialog_enhancer


def test_extract_box_text(qapp):
    box = QMessageBox()
    box.setWindowTitle("Alert Title")
    box.setText("Primary message.")
    box.setInformativeText("Additional context line.")
    box.setDetailedText("Stacktrace or details.")

    text = extract_box_text(box)
    assert "[Alert Title]" in text
    assert "Primary message." in text
    assert "Additional context line." in text
    assert "Stacktrace or details." in text


def test_dialog_enhancer_injects_copy_button_and_selectable_labels(qapp):
    install_dialog_enhancer(qapp)

    box = QMessageBox()
    box.setWindowTitle("Warning Window")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText("Something happened.")
    box.setInformativeText("Details here.")
    ok_btn = box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)

    # Schedule actions after dialog shows
    def on_show():
        # 1. Check labels are selectable
        labels = [lbl for lbl in box.findChildren(QLabel) if lbl.text()]
        assert len(labels) >= 2
        for lbl in labels:
            flags = lbl.textInteractionFlags()
            assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
            assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard

        # 2. Find injected copy button
        copy_btn = None
        for btn in box.findChildren(QPushButton):
            if "Copy text" in (btn.text() or ""):
                copy_btn = btn
                break
        assert copy_btn is not None

        # 3. Click copy button
        copy_btn.click()
        assert copy_btn.text() == "✓ Copied!"

        clipboard_text = qapp.clipboard().text()
        assert "[Warning Window]" in clipboard_text
        assert "Something happened." in clipboard_text
        assert "Details here." in clipboard_text

        # 4. Click OK and verify dialog closes with ok_btn
        ok_btn.click()

    QTimer.singleShot(50, on_show)
    box.exec()
    assert box.clickedButton() is ok_btn


def test_run_all_tab_dismiss_checkpoint(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from modules.updates import run_checkpoint as rc
    from modules.updates.run_all_tab import _RunAllTab

    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    rc.begin(tmp_path, ["winget"], "run_all")
    rc.item(tmp_path, "winget:TestApp", "started", "Testing")

    tab = _RunAllTab()
    tab.set_app(SimpleNamespace(app_data_dir=str(tmp_path)))
    tab.refresh_checkpoint_note()

    assert not tab._checkpoint_lbl.isHidden()
    assert hasattr(tab, "_checkpoint_dismiss_btn")

    # Click dismiss button
    tab._checkpoint_dismiss_btn.click()

    # Should be hidden now and checkpoint cleared
    assert tab._checkpoint_lbl.isHidden()
    assert rc.load(tmp_path) is None


def test_confirm_preflight_titles_and_dismiss(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from modules.updates import run_checkpoint as rc
    from modules.updates.run_all_tab import _RunAllTab
    from modules.updates import reboot_preflight

    # Case 1: unfinished run, no restart reasons
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    rc.begin(tmp_path, ["winget"], "run_all")
    rc.item(tmp_path, "winget:TestApp", "started", "Testing")

    monkeypatch.setattr(reboot_preflight, "_read_reboot", lambda: ([], False))

    tab = _RunAllTab()
    tab.set_app(SimpleNamespace(app_data_dir=str(tmp_path)))

    # Schedule click on "Dismiss previous run"
    def dismiss_action():
        active_box = None
        for w in qapp.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                active_box = w
                break
        assert active_box is not None
        assert active_box.windowTitle() == "Previous Run Incomplete"

        # Find dismiss button
        dismiss_btn = None
        for btn in active_box.findChildren(QPushButton):
            if "Dismiss previous run" in (btn.text() or ""):
                dismiss_btn = btn
                break
        assert dismiss_btn is not None
        dismiss_btn.click()

    QTimer.singleShot(50, dismiss_action)
    result = tab._confirm_preflight()
    assert result is False
    assert rc.load(tmp_path) is None
