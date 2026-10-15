"""The persistence points Windows' own list misses, and what is Windows'.

WMI event subscriptions, SilentProcessExit, Active Setup, print monitors and
netsh helpers are all persistence points the previous ten sources did not
cover. Every one of them also exists on every Windows machine as Microsoft's
own component, so each test here is as much about what is EXCLUDED as what is
found.
"""
import os
import winreg
from datetime import datetime

import pytest

from modules.startup_manager import persistence
from modules.startup_manager import trust as trustlib


# ---- fake registry ----------------------------------------------------------

class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRegistry:
    """A read-only hive: {path: {value_name: data}}."""

    def __init__(self, values):
        self.values = values

    def _children(self, path):
        prefix = path + "\\"
        found = {p[len(prefix):].split("\\")[0]
                 for p in self.values if p.startswith(prefix)}
        return sorted(found)

    def _exists(self, path):
        if path in self.values:
            return True
        prefix = path + "\\"
        return any(p.startswith(prefix) for p in self.values)

    def OpenKey(self, hive, path):
        if not self._exists(path):
            raise FileNotFoundError(path)
        return FakeKey(path)

    def EnumKey(self, key, index):
        names = self._children(key.path)
        if index >= len(names):
            raise OSError(22, "no more keys")
        return names[index]

    def EnumValue(self, key, index):
        items = list(self.values.get(key.path, {}).items())
        if index >= len(items):
            raise OSError(22, "no more values")
        name, data = items[index]
        return name, data, winreg.REG_SZ

    def QueryValueEx(self, key, name):
        values = self.values.get(key.path, {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], winreg.REG_SZ


@pytest.fixture
def fake_hive(monkeypatch):
    def install(values):
        reg = FakeRegistry(values)
        monkeypatch.setattr(persistence.winreg, "OpenKey", reg.OpenKey)
        monkeypatch.setattr(persistence.winreg, "EnumKey", reg.EnumKey)
        monkeypatch.setattr(persistence.winreg, "EnumValue", reg.EnumValue)
        monkeypatch.setattr(persistence.winreg, "QueryValueEx", reg.QueryValueEx)
        return reg
    return install


# ---- Task 4: WMI event subscriptions ----------------------------------------

_SCM_FILTER = {"Name": "SCM Event Log Filter", "Query": "SELECT * FROM X"}
_SCM_CONSUMER = {"Name": "SCM Event Log Consumer"}
_SCM_BINDING = {
    "Filter": '__EventFilter.Name="SCM Event Log Filter"',
    "Consumer": 'NTEventLogEventConsumer.Name="SCM Event Log Consumer"',
}


def fake_query(filters=None, consumers=None, bindings=None, boom=False):
    filters = filters or {}
    consumers = consumers or {}
    bindings = bindings or []

    def query(class_name):
        if boom:
            raise RuntimeError("access denied")
        if class_name == "__EventFilter":
            return list(filters.values())
        if class_name == "__FilterToConsumerBinding":
            return list(bindings)
        return list(consumers.get(class_name, []))
    return query


def test_windows_own_scm_subscription_is_not_a_finding():
    inv = persistence.Inventory()
    persistence.read_wmi_subscriptions(inv, query=fake_query(
        filters={_SCM_FILTER["Name"]: _SCM_FILTER},
        consumers={"NTEventLogEventConsumer": [_SCM_CONSUMER]},
        bindings=[_SCM_BINDING]))
    assert inv.items == [] and inv.problems == []


def test_a_command_line_consumer_is_a_warn_finding():
    updater_filter = {"Name": "Updater", "Query": "SELECT * FROM Y"}
    updater_consumer = {"Name": "Updater",
                        "CommandLineTemplate": "powershell -enc AAAA"}
    binding = {"Filter": '__EventFilter.Name="Updater"',
               "Consumer": 'CommandLineEventConsumer.Name="Updater"'}
    inv = persistence.Inventory()
    persistence.read_wmi_subscriptions(inv, query=fake_query(
        filters={_SCM_FILTER["Name"]: _SCM_FILTER, "Updater": updater_filter},
        consumers={"NTEventLogEventConsumer": [_SCM_CONSUMER],
                   "CommandLineEventConsumer": [updater_consumer]},
        bindings=[_SCM_BINDING, binding]))
    assert len(inv.items) == 1
    item = inv.items[0]
    assert item.source == "WMI Subscription"
    assert "powershell -enc AAAA" in item.command
    persistence.assess(item, datetime.now())
    assert any(n.severity == "warn" for n in item.notes)


def test_a_failed_query_is_a_problem_not_an_empty_result():
    inv = persistence.Inventory()
    persistence.read_wmi_subscriptions(inv, query=fake_query(boom=True))
    assert inv.items == []
    assert any(persistence._WMI_SUB_NAMESPACE in problem
               for problem in inv.problems)


# ---- Task 5: the four registry points ---------------------------------------

def test_silent_process_exit_armed_and_inactive(fake_hive):
    fake_hive({
        persistence._SPE_PATH + "\\notepad.exe":
            {"MonitorProcess": "C:\\tools\\mon.exe"},
        persistence._IFEO_PATH + "\\notepad.exe": {"GlobalFlag": 0x200},
        persistence._SPE_PATH + "\\calc.exe":
            {"MonitorProcess": "C:\\tools\\mon.exe"},
        persistence._IFEO_PATH + "\\calc.exe": {"GlobalFlag": 0},
    })
    inv = persistence.Inventory()
    persistence.read_silent_process_exit(inv)
    by_name = {item.name: item for item in inv.items}
    assert by_name["notepad.exe"].enabled is True
    assert by_name["calc.exe"].enabled is False
    for item in inv.items:
        persistence.assess(item, datetime.now())
    assert by_name["notepad.exe"].has("silentexit")
    assert by_name["calc.exe"].has("inactive")


def test_a_missing_silent_process_exit_key_is_nothing(fake_hive):
    fake_hive({})
    inv = persistence.Inventory()
    persistence.read_silent_process_exit(inv)
    assert inv.items == [] and inv.problems == []


def test_active_setup_stubpath_is_listed(fake_hive):
    fake_hive({persistence._ACTIVE_SETUP_ROOTS[0] + "\\{GUID}":
               {"StubPath": "C:\\evil\\run.exe", "DisplayName": "Evil Setup"}})
    inv = persistence.Inventory()
    persistence.read_active_setup(inv)
    assert len(inv.items) == 1
    assert inv.items[0].source == "Active Setup"
    assert inv.items[0].name == "Evil Setup"
    assert inv.items[0].command == "C:\\evil\\run.exe"


def test_a_print_monitor_bare_dll_resolves_to_system32(fake_hive):
    fake_hive({persistence._PRINT_MONITORS + "\\MyMon": {"Driver": "mymon.dll"}})
    inv = persistence.Inventory()
    persistence.read_print_monitors(inv)
    assert len(inv.items) == 1
    expected = os.path.join(persistence.system_root(), "System32", "mymon.dll")
    assert inv.items[0].command == expected


def test_a_netsh_helper_bare_dll_resolves_to_system32(fake_hive):
    fake_hive({persistence._NETSH_HELPERS: {"mynet": "mynet.dll"}})
    inv = persistence.Inventory()
    persistence.read_netsh_helpers(inv)
    assert len(inv.items) == 1
    assert inv.items[0].source == "Netsh Helper"
    expected = os.path.join(persistence.system_root(), "System32", "mynet.dll")
    assert inv.items[0].command == expected


def _signed(item, publisher):
    item.trust = trustlib.SIGNED
    item.publisher = publisher
    return item


def test_the_signature_filter_keeps_only_what_is_not_microsoft():
    evil = persistence.Item("evil", "", "SilentProcessExit", "Machine", "loc")
    evil.trust = trustlib.UNSIGNED
    windows = _signed(
        persistence.Item("ie4uinit", "", "Active Setup", "Machine", "loc"),
        "Microsoft Corporation")
    unknown = persistence.Item("x", "", "Print Monitor", "Machine", "loc")
    unknown.trust = trustlib.UNKNOWN
    untouched = _signed(
        persistence.Item("run", "", "Run", "Machine", "loc"),
        "Microsoft Corporation")
    inv = persistence.Inventory(items=[evil, windows, unknown, untouched])
    persistence._drop_what_windows_shipped(inv)
    assert [i.name for i in inv.items] == ["evil", "x", "run"]


# ---- real machine -----------------------------------------------------------

@pytest.mark.real_machine
def test_wmi_subscriptions_on_this_machine_have_only_windows_own():
    import pythoncom

    pythoncom.CoInitialize()
    try:
        inv = persistence.Inventory()
        persistence.read_wmi_subscriptions(inv)
    finally:
        pythoncom.CoUninitialize()
    assert inv.items == [] and inv.problems == []


@pytest.mark.real_machine
def test_collect_drops_windows_own_netsh_and_silent_process_exit():
    import pythoncom

    pythoncom.CoInitialize()
    try:
        inv = persistence.collect()
    finally:
        pythoncom.CoUninitialize()
    assert not [i for i in inv.items if i.source == "Netsh Helper"]
    assert not [i for i in inv.items if i.source == "SilentProcessExit"]
