"""Tests for the Universal Command Palette (Ctrl+K / Ctrl+P)."""
from unittest.mock import MagicMock
from PyQt6.QtCore import Qt
from ui.command_palette import CommandPalette


def test_command_palette_indexes_and_searches(qapp):
    class FakeModule:
        def __init__(self, name, group="TOOLS", desc="A diagnostic module"):
            self.name = name
            self.group = group
            self.description = desc
            self.icon = "🔍"

    modules = [
        FakeModule("Disk Health", "OPTIMIZE", "Check SMART status"),
        FakeModule("Event Viewer", "DIAGNOSE", "System event logs"),
    ]

    selected_calls = []

    def on_select(target, payload=None):
        selected_calls.append((target, payload))

    palette = CommandPalette(modules, on_select)
    assert len(palette._items) >= 2

    # Verify search for module
    palette._populate("disk")
    assert palette._list.count() >= 1
    first_item = palette._list.item(0)
    assert "Disk Health" in first_item.text()

    # Activate
    palette._activate(first_item)
    assert len(selected_calls) == 1
    assert selected_calls[0][0] == "Disk Health"

    # Verify search for quick fix or script
    palette._populate("dns")
    assert palette._list.count() >= 1
    dns_item = palette._list.item(0)
    assert any(term in dns_item.text().lower() for term in ("dns", "network"))
