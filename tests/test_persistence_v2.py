"""Persistence sources added in the audit/logging plan (Task 4: WMI)."""
import pytest

from modules.startup_manager import persistence as p

_SCM_BINDING = {"Filter": '__EventFilter.Name="SCM Event Log Filter"',
                "Consumer": 'NTEventLogEventConsumer.Name="SCM Event Log Consumer"'}
_SCM_FILTER = {"Name": "SCM Event Log Filter", "Query": "SELECT * FROM MSFT_SCMEventLogEvent"}
_SCM_CONSUMER = {"Name": "SCM Event Log Consumer", "__CLASS": "NTEventLogEventConsumer"}


def _fake(bindings=(), filters=(), consumers=()):
    data = {"__FilterToConsumerBinding": list(bindings), "__EventFilter": list(filters)}
    data["__EventConsumer"] = list(consumers)

    def query(cls):
        return list(data.get(cls, []))
    return query


def test_windows_own_pair_is_not_an_item():
    inv = p.Inventory()
    p.read_wmi_subscriptions(inv, _fake([_SCM_BINDING], [_SCM_FILTER], [_SCM_CONSUMER]))
    assert inv.items == [] and inv.problems == []


def test_command_line_consumer_is_listed_with_warn_note():
    cmd = {"Name": "Evil", "__CLASS": "CommandLineEventConsumer",
           "CommandLineTemplate": "powershell -enc AAAA"}
    flt = {"Name": "Updater", "Query": "SELECT * FROM __InstanceModificationEvent"}
    bind = {"Filter": '\\\\HOST\\root\\subscription:__EventFilter.Name="Updater"',
            "Consumer": '\\\\HOST\\root\\subscription:CommandLineEventConsumer.Name="Evil"'}
    inv = p.Inventory()
    p.read_wmi_subscriptions(inv, _fake([_SCM_BINDING, bind], [_SCM_FILTER, flt], [_SCM_CONSUMER, cmd]))
    assert len(inv.items) == 1
    item = inv.items[0]
    assert (item.source, item.scope, item.name) == ("WMI Subscription", "Machine", "Updater")
    assert item.command == "powershell -enc AAAA"
    assert item.extra.startswith("SELECT * FROM __InstanceModificationEvent")
    p.assess(item, p.datetime.now())
    assert item.flagged and item.has("wmi")


def test_same_names_with_other_consumer_class_is_not_skipped():
    cmd = {"Name": "SCM Event Log Consumer", "__CLASS": "CommandLineEventConsumer",
           "CommandLineTemplate": "calc.exe"}
    bind = {"Filter": _SCM_BINDING["Filter"],
            "Consumer": 'CommandLineEventConsumer.Name="SCM Event Log Consumer"'}
    inv = p.Inventory()
    p.read_wmi_subscriptions(inv, _fake([bind], [_SCM_FILTER], [cmd]))
    assert len(inv.items) == 1


def test_unbound_filter_and_consumer_are_listed():
    flt = {"Name": "Orphan", "Query": "SELECT 1"}
    cmd = {"Name": "Lonely", "__CLASS": "ActiveScriptEventConsumer", "ScriptText": "x"}
    inv = p.Inventory()
    p.read_wmi_subscriptions(inv, _fake([_SCM_BINDING], [_SCM_FILTER, flt], [_SCM_CONSUMER, cmd]))
    assert sorted(i.name for i in inv.items) == ["Lonely", "Orphan"]
    assert all("not bound" in i.extra for i in inv.items)


def test_query_failure_is_a_problem_not_empty():
    def boom(cls):
        raise OSError("access denied")
    inv = p.Inventory()
    p.read_wmi_subscriptions(inv, boom)
    assert inv.items == [] and any("root\\subscription" in x for x in inv.problems)


def test_real_machine_has_only_windows_own_subscription():
    pythoncom = pytest.importorskip("pythoncom")
    pythoncom.CoInitialize()
    try:
        inv = p.Inventory()
        p.read_wmi_subscriptions(inv)
    finally:
        pythoncom.CoUninitialize()
    assert inv.problems == [] and inv.items == []
