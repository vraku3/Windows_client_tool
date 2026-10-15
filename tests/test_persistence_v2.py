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


def _one(binding, filters, consumers):
    inv = p.Inventory()
    p.read_wmi_subscriptions(inv, _fake([binding], filters, consumers))
    return inv


def test_scm_pair_excluded_in_single_quote_and_other_case_forms():
    for flt, con in (
        ("__EventFilter.Name='SCM Event Log Filter'", "NTEventLogEventConsumer.Name='SCM Event Log Consumer'"),
        ('__eventfilter.name="scm event log filter"', 'ntEventLogEventConsumer.NAME="scm event log consumer"'),
    ):
        inv = _one({"Filter": flt, "Consumer": con}, [_SCM_FILTER], [_SCM_CONSUMER])
        assert inv.items == []


def test_single_quoted_and_cased_reference_finds_malicious_consumer():
    cmd = {"Name": "Evil", "__CLASS": "CommandLineEventConsumer", "CommandLineTemplate": "evil.exe -x"}
    flt = {"Name": "Updater", "Query": "SELECT 1"}
    bind = {"Filter": "__EventFilter.Name='updater'", "Consumer": "commandlineeventconsumer.Name='EVIL'"}
    inv = _one(bind, [flt], [cmd])
    assert len(inv.items) == 1 and inv.items[0].command == "evil.exe -x"
    assert "not bound" not in inv.items[0].extra


def test_escaped_quotes_and_backslashes_in_names():
    cmd = {"Name": 'a"b\\c', "__CLASS": "CommandLineEventConsumer", "CommandLineTemplate": "x.exe"}
    bind = {"Filter": '__EventFilter.Name="f"', "Consumer": 'CommandLineEventConsumer.Name="a\\"b\\\\c"'}
    inv = _one(bind, [{"Name": "f", "Query": "q"}], [cmd])
    assert len(inv.items) == 1 and inv.items[0].command == "x.exe"
    bind2 = {"Filter": "__EventFilter.Name='f'", "Consumer": "CommandLineEventConsumer.Name='it\\'s'"}
    cmd2 = {"Name": "it's", "__CLASS": "CommandLineEventConsumer", "CommandLineTemplate": "y.exe"}
    assert _one(bind2, [{"Name": "f", "Query": "q"}], [cmd2]).items[0].command == "y.exe"


def _cl(**kw):
    row = {"Name": "C", "__CLASS": "CommandLineEventConsumer"}
    row.update(kw)
    bind = {"Filter": '__EventFilter.Name="F"', "Consumer": 'CommandLineEventConsumer.Name="C"'}
    return _one(bind, [{"Name": "F", "Query": "q"}], [row]).items[0]


def test_command_line_consumer_template_only():
    item = _cl(CommandLineTemplate="a.exe /s")
    assert item.command == "a.exe /s" and item.wmi_exe == ""


def test_command_line_consumer_executable_only():
    item = _cl(ExecutablePath="C:\\x\\real.exe")
    assert item.command == "C:\\x\\real.exe" and item.wmi_exe == "C:\\x\\real.exe"


def test_command_line_consumer_both_differ_shows_both():
    item = _cl(CommandLineTemplate="decoy.exe", ExecutablePath="C:\\x\\real.exe")
    assert item.command == "decoy.exe" and "C:\\x\\real.exe" in item.extra
    p.enrich(p.Inventory(items=[item]), {}, p.datetime.now())
    assert item.exe == "C:\\x\\real.exe" and item.exists is False and item.has("missing")


def test_active_script_fallback_and_warn_and_other_classes_info():
    def one(cls, **kw):
        row = {"Name": "C", "__CLASS": cls}
        row.update(kw)
        bind = {"Filter": '__EventFilter.Name="F"', "Consumer": cls + '.Name="C"'}
        item = _one(bind, [{"Name": "F", "Query": "q"}], [row]).items[0]
        p.assess(item, p.datetime.now())
        return item
    script = one("ActiveScriptEventConsumer", ScriptFileName="C:\\s.vbs")
    assert script.command == "C:\\s.vbs" and script.flagged
    for cls in ("NTEventLogEventConsumer", "SMTPEventConsumer"):
        item = one(cls)
        assert item.has("wmi") and not item.flagged
