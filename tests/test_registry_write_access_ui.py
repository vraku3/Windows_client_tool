"""Wiring for the "who can write here" label added to Registry Explorer's
value panel, next to the existing last-write-time label. Real-machine check:
selecting HKLM's own root (no security-name mapping without a subpath is
still a hive GetNamedSecurityInfo answers for) must not throw, and clicking
into the real Run key must show the label without ever swallowing an error."""
from PyQt6.QtCore import Qt

from modules.registry_explorer.registry_module import RegistryExplorerModule


def _make_module():
    module = RegistryExplorerModule()
    module.app = type("App", (), {"thread_pool": None})()
    module.create_widget()
    return module


def test_write_access_label_exists_and_starts_empty(qapp):
    module = _make_module()
    assert module._write_access_label.text() == ""


def test_selecting_hklm_root_populates_write_access_without_raising(qapp):
    module = _make_module()
    root_idx = module._model.index(0, 0)  # HKEY_LOCAL_MACHINE
    module._tree.setCurrentIndex(root_idx)
    text = module._write_access_label.text()
    assert text.startswith("Write access:")


def test_administrators_and_system_are_not_flagged_unusual_on_the_run_key(qapp):
    module = _make_module()
    module._update_write_access(
        r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    )
    text = module._write_access_label.text()
    assert "Administrators" in text or "SYSTEM" in text
    assert module._write_access_label.objectName() == "muted"


def test_a_refused_key_shows_the_reason_not_a_default(qapp):
    from core.admin_utils import is_admin

    module = _make_module()
    module._update_write_access(r"HKEY_LOCAL_MACHINE\SAM\SAM")
    text = module._write_access_label.text()
    if not is_admin():
        # Refused unelevated (measured) -- the reason must show, never a
        # silent "none granted" that reads as a clean bill of health.
        assert "could not read" in text
        assert module._write_access_label.objectName() == "muted"
