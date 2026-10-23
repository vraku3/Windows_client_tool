"""Explanation coverage tests kept inside the batch's owned directory."""
import json
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from modules.tweaks.tweaks_help import (
    CONTROL_HELP, PRESET_HELP, RISK_HELP, SELECTION_HELP, STATUS_HELP,
    row_help, step_summary, undo_help,
)
from modules.tweaks.tweaks_module import TweakRow
from ui.tooltip_audit import audit_modules


@pytest.fixture(scope="session", autouse=True)
def help_qapp():
    # Keep Qt alive across this directory and tests/conftest.py's session.
    app = QApplication.instance() or QApplication([])
    yield app


def test_tweaks_has_no_explanation_gaps(tmp_path):
    app = QApplication.instance() or QApplication([])
    rows = audit_modules(str(tmp_path), "Tweaks")
    assert rows and all(not row["error"] and not row["gaps"] for row in rows), rows
    assert app is not None


def test_help_dictionaries_are_complete():
    for mapping in (CONTROL_HELP, PRESET_HELP, RISK_HELP, SELECTION_HELP, STATUS_HELP):
        assert mapping and all(value.strip() for value in mapping.values())


def test_all_catalog_descriptions_are_expanded():
    for path in (Path(__file__).parent / "definitions").glob("*.json"):
        for tweak in json.loads(path.read_text(encoding="utf-8")):
            assert len(tweak["description"]) >= 60, tweak["id"]


@pytest.mark.parametrize("status", list(STATUS_HELP))
def test_row_help_survives_detection_updates(status):
    app = QApplication.instance() or QApplication([])
    tweak = {"name": "Example", "description": "Explains the example setting.",
             "risk": "medium", "requires_admin": False,
             "steps": [{"type": "registry", "key": r"HKCU\Software\Example",
                        "value": "Enabled", "data": 0}]}
    row = TweakRow(tweak)
    row.set_status(status, "Example detection reason")
    for widget in (row, row.checkbox, row.apply_btn, row.disable_btn):
        help_text = widget.toolTip()
        for expected in (tweak["description"], RISK_HELP["medium"], "HKCU\\Software\\Example",
                         "Revert", "Administrator", STATUS_HELP[status], "Example detection reason"):
            assert expected in help_text
    assert "Example detection reason" in row.status_label.toolTip()
    row.close()
    row.deleteLater()
    assert app is not None


def test_undo_help_distinguishes_irreversible_and_recorded_steps():
    tweak = {"steps": [{"type": "appx", "package": "Example.App"},
                       {"type": "command", "cmd": "example"},
                       {"type": "service", "name": "Example", "start_type": "disabled"},
                       {"type": "scheduled_task", "task_name": "ExampleTask"},
                       {"type": "script", "command": "example", "revert_command": "undo-example"}]}
    text = undo_help(tweak)
    assert "App removals cannot be undone" in text
    assert "Commands and scripts without an explicit undo command cannot be undone" in text
    assert "not its previous running/stopped state" in text
    assert "script's supplied undo command" in text
    assert "scheduled-task states" in text
    assert "Removes installed app package Example.App" == step_summary(tweak["steps"][0])
    assert "Disables scheduled task ExampleTask" == step_summary(tweak["steps"][3])
    assert "Unknown:" in row_help(tweak)
