"""System Management as a host for scheduled tasks, services, and Windows
optional features.

The decision this pins: three unrelated-by-code, thematically-similar
ModuleGroup.MANAGE panes ("a list of OS-managed things you toggle on/off")
become one sidebar entry with three tabs, per this app's own established
CompositeModule pattern (Diagnose, Debloat, Startup & Boot, Network
Diagnostics). Pure re-hosting: none of the three children change at all.
"""
import pytest

from modules.system_management.system_management_module import SystemManagementModule


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


def test_process_is_no_longer_registered_on_its_own(qapp):
    """The absorption, checked where it actually matters: the sidebar."""
    import inspect

    import main

    source = inspect.getsource(main.register_all_modules)
    assert "register(TasksModule())" not in source
    assert "register(ServicesModule())" not in source
    assert "register(WindowsFeaturesModule())" not in source
    assert "register(SystemManagementModule())" in source
