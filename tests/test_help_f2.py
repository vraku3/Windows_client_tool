"""Batch F2 explanation regression tests kept inside the assigned ownership scope."""
import importlib
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def help_qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize("name", ["Debloat", "App Buster", "Store Apps", "Game Mode", "Security Dashboard"])
def test_owned_modules_have_no_help_gaps(name, tmp_path, help_qapp):
    from ui.tooltip_audit import audit_modules
    from modules.debloat.debloat_module import DebloatModule
    from modules.app_buster.app_buster_module import AppBusterModule
    from modules.store_apps.store_apps_module import StoreAppsModule
    from modules.game_mode.game_mode_module import GameModeModule
    from modules.security_dashboard.security_module import SecurityDashboardModule
    classes = {c.name: c for c in (DebloatModule, AppBusterModule, StoreAppsModule,
                                   GameModeModule, SecurityDashboardModule)}
    # Do not import or construct other workers' modules while they are being edited.
    with patch("main.register_all_modules", lambda app: app.module_registry.register(classes[name]())):
        rows = audit_modules(str(tmp_path), name)
    assert rows
    assert all(not row.get("error") and row["total"] == 0 for row in rows), rows


@pytest.mark.parametrize("area", ["debloat", "app_buster", "store_apps", "game_mode", "security_dashboard"])
def test_help_dictionaries_have_no_empty_entries(area):
    # Debloat's text moved to ui/help (shared with App Buster and Store Apps).
    module = importlib.import_module("ui.help.debloat_text" if area == "debloat" else f"modules.{area}.help_text")
    dictionaries = [value for key, value in vars(module).items()
                    if key.isupper() and isinstance(value, dict)]
    assert dictionaries
    for entries in dictionaries:
        assert all(isinstance(value, str) and value.strip() for value in entries.values())


def test_every_security_card_explains_protection_compatibility_and_revert(help_qapp):
    from modules.security_dashboard.catalog import load_catalog
    from modules.security_dashboard.security_module import ControlCard
    from modules.security_dashboard.help_text import compatibility
    catalog = load_catalog()
    assert len(catalog) >= 181
    for control in catalog.values():
        with patch.object(type(control), "read", side_effect=AssertionError("help must not read Windows")):
            card = ControlCard(control)
            text = card.toolTip()
            assert control.description in text and control.why_it_matters in text
            assert compatibility(control) in text
            assert "Revert" in text
            assert card.toggle_button.toolTip() == text
            card.deleteLater()


def test_security_revert_help_distinguishes_unmapped_threat_action(help_qapp):
    from modules.security_dashboard.catalog import load_catalog
    from modules.security_dashboard.security_module import ControlCard
    card = ControlCard(load_catalog()["defender_threat_severe"])
    card.set_reading(0)
    assert "cannot restore every step exactly" in card.toolTip()
    card.set_reading(1)
    assert "can restore the captured setting" in card.toolTip()
    card.deleteLater()


def test_optimise_capability_help_explains_optional_and_windows_update():
    from modules.game_mode.engine.optimise import ITEMS
    from modules.game_mode.help_text import setting_help
    capabilities = [spec for spec in ITEMS if spec.kind == "capability"]
    assert capabilities
    for spec in capabilities:
        text = setting_help(spec, "")
        assert "Windows Update" in text and "payload is not saved" in text
        if spec.level == "O":
            assert "Optional" in text
