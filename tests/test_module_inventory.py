"""What the sidebar is made of.

These assert the shape of the module list itself — how many entries there are,
that no two share a name, that a module which became a tab is still reachable,
and that every search source the filter panel offers can actually answer.
"""
import sys
import time

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

import main as app_main

# `_all_composite_children()` is called directly as a `parametrize` argument
# below, twice, so it builds full module trees at COLLECTION time -- before
# any pytest fixture, including conftest's session-scoped `qapp`, has run
# (fixtures only activate once a test body executes). A QObject constructed
# with no QApplication in existence yet (e.g. DebloatToolsModule's own
# `self._signals = _Signals()`) is later reported by sip as already deleted
# the first time something tries to use its signals -- `create_widget()`'s
# `.connect()` call -- even though nothing explicitly destroyed it. This is
# the same hazard test_module_smoke.py's own module-level comment names
# ("...so make sure one exists here too"), but its one-line guard doesn't
# actually protect anything: a bare `QApplication.instance() or
# QApplication(sys.argv)` expression with no assignment is immediately
# garbage-collected once that statement finishes (confirmed empirically --
# `QApplication.instance()` reports None again moments later), so the
# reference must be kept somewhere for the life of the session. A module
# level global does that.
_module_inventory_qapp = QApplication.instance() or QApplication(sys.argv)


class _FakeRegistry:
    def __init__(self):
        self.modules = []

    def register(self, module):
        self.modules.append(module)


class _FakeConfig:
    """A working, in-memory stand-in for ConfigManager.

    Several composite children (DebloatToolsModule, StoreAppsModule) call
    `self.app.config.get(...)` unconditionally from `create_widget()` --
    unlike PerfMon or RemoteToolsModule, which guard with `if app.config:` /
    `getattr(app, "config", None) is not None` first. `config = None` made
    that gap invisible until `test_every_composite_child_can_build_its_own_widget`
    started actually calling `create_widget()` on every composite child.
    """

    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value) -> None:
        self._data[key] = value


class _FakeApp:
    """Enough App for on_start. Modules store the reference and little else."""

    def __init__(self):
        self.module_registry = _FakeRegistry()
        self.backup = None          # DebloatToolsModule builds a TweakEngine on it
        self.config = _FakeConfig()  # real get()/set(); several children call it unguarded
        self.search = _FakeSearch()
        self.thread_pool = None


class _FakeSearch:
    def __init__(self):
        self.registered = []

    def register_provider(self, provider):
        self.registered.append(provider)


@pytest.fixture
def registered():
    app = _FakeApp()
    app_main.register_all_modules(app)
    return app.module_registry.modules


def test_duplicate_finder_is_gone(registered):
    assert "Duplicate Finder" not in {m.name for m in registered}


def test_nothing_imports_the_duplicate_finder_package():
    with pytest.raises(ModuleNotFoundError):
        __import__("modules.duplicate_finder.duplicate_finder_module")


def test_module_names_are_unique(registered):
    names = [m.name for m in registered]
    assert len(names) == len(set(names))


def test_store_apps_is_a_debloat_tab_not_a_sidebar_entry(registered):
    names = {m.name for m in registered}
    assert "Store Apps" not in names
    debloat = next(m for m in registered if m.name == "Debloat")
    assert [c.name for c in debloat.children] == ["Debloat", "Store Apps"]


def test_store_apps_is_still_reachable_by_name(registered):
    from core.module_registry import ModuleRegistry

    registry = ModuleRegistry()
    for module in registered:
        registry.register(module)

    assert registry.route_map()["Store Apps"] == ("Debloat", 1)


def test_startup_and_boot_hosts_the_three_boot_modules(registered):
    names = {m.name for m in registered}
    assert "Boot Analyzer" not in names
    assert "Power & Boot" not in names
    host = next(m for m in registered if m.name == "Startup & Boot")
    assert [c.name for c in host.children] == [
        "Startup Manager", "Boot Analyzer", "Power & Boot",
    ]
    assert host.group == "SYSTEM"


def test_network_extras_no_longer_carries_its_own_hosts_editor(qapp):
    """There were two HOSTS editors: this tab, and the Hosts Editor module."""
    from modules.network_extras.net_extras_module import NetExtrasModule

    widget = NetExtrasModule().create_widget()
    labels = [widget.tabText(i) for i in range(widget.count())]

    assert labels == ["DNS Switcher", "Proxy Settings", "Quick Actions"]


def _module_classes_that_teardown_ui():
    """Modules whose teardown path touches widgets create_widget() builds."""
    from modules.certificate_viewer.cert_module import CertModule
    from modules.cleanup.cleanup_module import CleanupModule
    from modules.wifi_analyzer.wifi_module import WifiAnalyzerModule

    return [WifiAnalyzerModule, CertModule, CleanupModule]


@pytest.mark.parametrize(
    "module_class",
    _module_classes_that_teardown_ui(),
    ids=lambda c: c.__name__,
)
def test_a_module_survives_being_stopped_without_ever_being_built(qapp, module_class):
    """`on_stop` runs for modules whose widget was never built.

    Nothing hit this while every module's widget was built eagerly at startup
    by register_module. A composite builds a child's widget only when its tab
    is first shown, and still stops every started child — so a tab nobody
    opened takes this path.

    wifi_module._stop_scan reached self._progress and CertModule.on_deactivate
    reached self._tabs; both are created in create_widget().
    """
    module = module_class()
    module.on_start(_FakeApp())

    module.on_deactivate()   # must not raise
    module.refresh_data()    # the auto-refresh timer takes this path too
    module.on_stop()         # must not raise


def test_system_management_hosts_the_three_managed_modules(registered):
    names = {m.name for m in registered}
    assert "Scheduled Tasks" not in names
    assert "Services" not in names
    assert "Windows Features" not in names
    host = next(m for m in registered if m.name == "System Management")
    assert [c.name for c in host.children] == [
        "Scheduled Tasks", "Services", "Windows Features",
    ]


def test_services_the_composite_child_wins_the_route_over_the_dashboard_tab(
    registered,
):
    """dashboard/services_module.py and services_manager/services_module.py
    both declare name = "Services" -- one a Dashboard-internal process-view
    tab, one the real System Management child. route_map() resolves
    same-name collisions by registration order in main.py; this pins the
    current, correct outcome so reordering main.py doesn't silently
    misroute global "Services" navigation to the wrong pane."""
    from core.module_registry import ModuleRegistry
    from modules.services_manager.services_module import (
        ServicesModule as RealServicesModule,
    )

    registry = ModuleRegistry()
    for module in registered:
        registry.register(module)

    host_name, tab_index = registry.route_map()["Services"]
    assert host_name == "System Management"
    host = next(m for m in registered if m.name == host_name)
    assert isinstance(host.children[tab_index], RealServicesModule)


def test_the_network_tools_are_tabs_of_network_diagnostics(registered):
    names = {m.name for m in registered}
    for gone in (
        "Wi-Fi Analyzer", "Hosts Editor", "Network Extras",
        "Shared Resources", "Remote Tools",
    ):
        assert gone not in names
    host = next(m for m in registered if m.name == "Network Diagnostics")
    assert [c.name for c in host.children] == [
        "Network Diagnostics", "Wi-Fi Analyzer", "Hosts Editor", "Network Extras",
        "Shared Resources", "Remote Tools",
    ]


def test_diagnose_hosts_the_six_log_readers(registered):
    diagnose = next(m for m in registered if m.name == "Diagnose")
    assert [c.name for c in diagnose.children] == [
        "Event Viewer", "CBS Log", "DISM Log",
        "Windows Update", "Reliability", "Crash Dumps",
    ]


def test_every_diagnostic_source_is_back_in_global_search(registered):
    """The regression: Diagnose returned None, so these six reached nothing."""
    diagnose = next(m for m in registered if m.name == "Diagnose")
    names = {p.module_name for p in diagnose.get_search_providers()}
    assert names == {
        "EventViewer", "CBS", "DISM", "WindowsUpdate", "Reliability", "CrashDumps",
    }


def test_the_filter_panel_offers_no_source_that_cannot_answer(registered):
    """filter_panel listed six sources whose providers were wired to nothing."""
    from ui.filter_panel import _ALL_SOURCES

    reachable = set()
    for module in registered:
        reachable.update(p.module_name for p in module.get_search_providers())

    assert set(_ALL_SOURCES) <= reachable


def test_the_sidebar_is_28_entries(registered):
    """28, down from 32: the full history of the Management + Network
    consolidation (see
    docs/superpowers/specs/2026-09-16-management-network-consolidation-design.md).
    Task 1 folded three previously-independent ModuleGroup.MANAGE entries --
    Scheduled Tasks, Services, Windows Features -- into one new "System
    Management" hub: 32 - 3 + 1 = 30. Task 2 (this one) folds two more
    previously-independent ModuleGroup.TOOLS entries -- Shared Resources,
    Remote Tools -- into the already-existing "Network Diagnostics" hub as
    two more tabs, with no new hub created: 30 - 2 = 28. Absorbed modules are
    still reachable -- as tabs, which `_all_composite_children` covers."""
    assert len(registered) == 28


def test_process_explorer_is_reachable_as_a_dashboard_tab(registered):
    """Absorbed, not deleted."""
    dashboard = next(m for m in registered if m.name == "Dashboard")
    assert "Process Explorer" in [c.name for c in dashboard.children]


def test_perfmon_is_a_dashboard_tab_not_a_sidebar_entry(registered):
    """Merged with the Dashboard: PerfMon's charts/alerts/live monitor live
    on a Dashboard tab, so the sidebar does not double it."""
    names = {m.name for m in registered}
    assert "PerfMon" not in names
    dashboard = next(m for m in registered if m.name == "Dashboard")
    assert "PerfMon" in [c.name for c in dashboard.children]


def _all_composite_children():
    """Every child of every registered composite, as (host, child) pairs."""
    app = _FakeApp()
    app_main.register_all_modules(app)
    pairs = []
    for module in app.module_registry.modules:
        for child in getattr(module, "children", []):
            pairs.append((module.name, child))
    return pairs


@pytest.mark.parametrize(
    "host_name,child",
    _all_composite_children(),
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_every_composite_child_survives_a_tick_it_was_not_built_for(
    qapp, host_name, child
):
    """The host's auto-refresh timer can tick before a tab was ever opened.

    CompositeModule.refresh_data forwards to the visible child, and the very
    first tab is built at create_widget() — but any other child can be current
    without ever having been shown, and on_stop reaches all of them. Every one
    of these paths must survive a missing widget.
    """
    child.on_start(_FakeApp())

    child.refresh_data()     # must not raise
    child.on_deactivate()    # must not raise
    child.on_stop()          # must not raise


@pytest.mark.parametrize(
    "host_name,child",
    _all_composite_children(),
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_every_composite_child_can_build_its_own_widget(host_name, child, qapp):
    """test_every_composite_child_survives_a_tick_it_was_not_built_for only
    proves a child doesn't raise when ticked unbuilt -- it never actually
    calls create_widget(), so a module whose create_widget() itself is
    broken (or, as WindowsFeaturesModule's own dead refresh_data guard
    showed, silently does nothing useful) could still pass every existing
    composite test. This one actually builds each child's widget.

    Some children (PowerBootModule's own `load_power`/`load_boot`) start a
    real background Worker synchronously inside `create_widget()`. This
    function's own `widget` local keeps the returned widget alive only for
    the CALL -- once it returns, nothing here reparents it the way a real
    composite host's `wrap()` would, so it becomes collectible while that
    worker's queued result signal is still in flight. Delivering a queued
    signal into a since-destroyed QWidget is not a Python exception, it is
    a hard process crash (reproduced standalone as `0xC0000409`, this
    session, from exactly this shape). Draining the thread pool WHILE
    `widget` is still a live local -- before this function returns --
    means any such worker resolves against a still-live widget instead of
    a torn-down one, for every composite child this parametrizes over, not
    just the one that happened to be found and fixed this time."""
    child.on_start(_FakeApp())
    widget = child.create_widget()
    assert widget is not None
    QThreadPool.globalInstance().waitForDone(10_000)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_leaving_a_module_and_coming_back_restarts_its_live_timer(qapp):
    """PerfMon crashed on re-entry and left its live monitor dead.

    on_deactivate deleteLater()s _live_timer and sets it to None, but
    on_activate guarded with hasattr() — true for an attribute whose value is
    None. Opening PerfMon, leaving, and returning raised AttributeError.
    """
    from modules.perfmon.perfmon_module import PerfMonModule

    module = PerfMonModule()
    module.on_start(_FakeApp())
    module.create_widget()

    module.on_activate()
    assert module._live_timer.isActive()

    module.on_deactivate()
    assert module._live_timer is None

    module.on_activate()               # used to raise AttributeError
    assert module._live_timer is not None
    assert module._live_timer.isActive()
