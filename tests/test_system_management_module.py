"""System Management as a host for scheduled tasks, services, and Windows
optional features.

The decision this pins: three unrelated-by-code, thematically-similar
ModuleGroup.MANAGE panes ("a list of OS-managed things you toggle on/off")
become one sidebar entry with three tabs, per this app's own established
CompositeModule pattern (Diagnose, Debloat, Startup & Boot, Network
Diagnostics). Pure re-hosting: none of the three children change at all.
"""
import pytest

import main as app_main
from modules.system_management.system_management_module import SystemManagementModule


class _FakeRegistry:
    def __init__(self):
        self.modules = []

    def register(self, module):
        self.modules.append(module)


class _FakeSearch:
    def __init__(self):
        self.registered = []

    def register_provider(self, provider):
        self.registered.append(provider)


class _FakeApp:
    """Enough App for on_start. Matches test_module_inventory.py's fixture."""

    def __init__(self):
        self.module_registry = _FakeRegistry()
        self.backup = None
        self.config = None
        self.search = _FakeSearch()
        self.thread_pool = None


@pytest.fixture
def registered():
    app = _FakeApp()
    app_main.register_all_modules(app)
    return app.module_registry.modules


@pytest.fixture
def module():
    return SystemManagementModule()


def test_the_hub_has_three_children(module):
    assert len(module.children) == 3
    names = {type(c).__name__ for c in module.children}
    assert names == {"TasksModule", "ServicesModule", "WindowsFeaturesModule"}


def test_the_hub_is_not_gated_behind_elevation(module):
    """Scheduled Tasks needs no elevation at all; Services and Windows
    Features do -- gating the whole hub on admin would hide the one child
    that works fine unelevated. CompositeModule disables the admin-only
    children individually instead."""
    assert module.requires_admin is False


def test_the_hub_builds_a_widget_with_all_three_tabs(qapp):
    class FakeApp:
        backup = None
        config = None
        thread_pool = None

    mod = SystemManagementModule()
    mod.on_start(FakeApp())
    widget = mod.create_widget()
    assert widget is not None
    assert widget.count() == 3


def test_the_three_children_are_no_longer_registered_on_their_own(registered):
    """The absorption, checked where it actually matters: the sidebar."""
    names = {m.name for m in registered}
    assert "Scheduled Tasks" not in names
    assert "Services" not in names
    assert "Windows Features" not in names
    assert "System Management" in names
