"""What the sidebar is made of.

These assert the shape of the module list itself — how many entries there are,
that no two share a name, that a module which became a tab is still reachable,
and that every search source the filter panel offers can actually answer.
"""
import pytest

import main as app_main


class _FakeRegistry:
    def __init__(self):
        self.modules = []

    def register(self, module):
        self.modules.append(module)


class _FakeApp:
    """Enough App for on_start. Modules store the reference and little else."""

    def __init__(self):
        self.module_registry = _FakeRegistry()
        self.backup = None          # DebloatToolsModule builds a TweakEngine on it
        self.config = None          # PerfMon reads alert config off it
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


def test_the_network_tools_are_tabs_of_network_diagnostics(registered):
    names = {m.name for m in registered}
    for gone in ("Wi-Fi Analyzer", "Hosts Editor", "Network Extras"):
        assert gone not in names
    host = next(m for m in registered if m.name == "Network Diagnostics")
    assert [c.name for c in host.children] == [
        "Network Diagnostics", "Wi-Fi Analyzer", "Hosts Editor", "Network Extras",
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


def test_the_sidebar_is_33_entries(registered):
    """33 since Monitor Control was added.

    32, not 34: Process Explorer stopped being its own sidebar entry when
    the Dashboard absorbed it, and PerfMon became a Dashboard tab too. Two
    doors to the same room is two places to kill a process from. Absorbed
    modules are still reachable -- as Dashboard tabs, which
    `_all_composite_children` covers."""
    assert len(registered) == 33


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
