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


# ---- Task 5: SilentProcessExit, Active Setup, Print Monitors, Netsh helpers ----

import os  # noqa: E402
import winreg as _wr  # noqa: E402

from modules.startup_manager import trust as _trust  # noqa: E402
from core.windows_utils import system_root  # noqa: E402

_HIVES = {_wr.HKEY_LOCAL_MACHINE: "HKLM", _wr.HKEY_CURRENT_USER: "HKCU"}


class _FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_registry(monkeypatch, tree, refused=()):
    """tree: {"HKLM\\path": {value: data}}; a key exists when it or any child is listed."""
    orig = {k.casefold(): k for k in tree}
    tree = {k.casefold(): v for k, v in tree.items()}
    refused = {r.casefold() for r in refused}

    def children(path):
        pre = path + "\\"
        return sorted({orig[k][len(pre):].split("\\")[0] for k in tree if k.startswith(pre)})

    def open_key(base, sub, *a):
        path = (base.path if isinstance(base, _FakeKey) else _HIVES[base]) + ("\\" + sub if sub else "")
        path = path.casefold()
        if path in refused:
            raise PermissionError(5, "Access is denied")
        if path not in tree and not children(path):
            raise FileNotFoundError(2, "not found")
        return _FakeKey(path)

    def enum_key(key, i):
        names = children(key.path)
        if i >= len(names):
            raise OSError(259, "no more")
        return names[i]

    def enum_value(key, i):
        items = list(tree.get(key.path, {}).items())
        if i >= len(items):
            raise OSError(259, "no more")
        return items[i][0], items[i][1], 1

    def query(key, name):
        vals = {k.casefold(): v for k, v in tree.get(key.path, {}).items()}
        if name.casefold() not in vals:
            raise FileNotFoundError(2, "no value")
        return vals[name.casefold()], 1

    monkeypatch.setattr(p.winreg, "OpenKey", open_key)
    monkeypatch.setattr(p.winreg, "EnumKey", enum_key)
    monkeypatch.setattr(p.winreg, "EnumValue", enum_value)
    monkeypatch.setattr(p.winreg, "QueryValueEx", query)


_SPE = "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SilentProcessExit"
_IFEO = "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options"


def test_silent_process_exit_active_and_inactive(monkeypatch):
    _fake_registry(monkeypatch, {
        _SPE + "\\a.exe": {"MonitorProcess": "C:\\x\\evil.exe"},
        _SPE + "\\b.exe": {"MonitorProcess": "C:\\x\\other.exe"},
        _IFEO + "\\a.exe": {"GlobalFlag": 0x200},
        _IFEO + "\\b.exe": {"GlobalFlag": 0x100},
        _SPE + "\\c.exe": {"ReportingMode": 1},
    })
    inv = p.Inventory()
    p.read_silent_process_exit(inv)
    by = {i.name: i for i in inv.items}
    assert set(by) == {"a.exe", "b.exe"} and inv.problems == []
    assert by["a.exe"].source == "SilentProcessExit" and by["a.exe"].command == "C:\\x\\evil.exe"
    assert "a.exe exits" in by["a.exe"].extra and not by["a.exe"].inactive
    assert by["b.exe"].inactive == "inactive: GlobalFlag lacks 0x200"
    p.assess(by["b.exe"], p.datetime.now())
    assert any(n.code == "silentexit" and n.severity == "info" and "inactive: GlobalFlag lacks 0x200" in n.text
               for n in by["b.exe"].notes)


def test_missing_keys_are_not_problems(monkeypatch):
    _fake_registry(monkeypatch, {})
    inv = p.Inventory()
    for reader in (p.read_silent_process_exit, p.read_active_setup,
                   p.read_print_monitors, p.read_netsh_helpers):
        reader(inv)
    assert inv.items == [] and inv.problems == []


def test_refused_key_is_a_problem(monkeypatch):
    _fake_registry(monkeypatch, {"HKLM\\SOFTWARE\\Microsoft\\NetSh": {"x": "a.dll"}},
                   refused=["HKLM\\SOFTWARE\\Microsoft\\NetSh"])
    inv = p.Inventory()
    p.read_netsh_helpers(inv)
    assert inv.items == [] and len(inv.problems) == 1 and "NetSh" in inv.problems[0]


def test_active_setup_stubpath_both_views(monkeypatch):
    base = "HKLM\\SOFTWARE\\Microsoft\\Active Setup\\Installed Components"
    wow = "HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Active Setup\\Installed Components"
    _fake_registry(monkeypatch, {
        base + "\\{G1}": {"": "Some Product", "StubPath": "\"C:\\P\\stub.exe\" /user"},
        base + "\\{G2}": {"Version": "1"},
        wow + "\\{G3}": {"StubPath": "C:\\Q\\w.exe"},
    })
    inv = p.Inventory()
    p.read_active_setup(inv)
    by = {i.name: i for i in inv.items}
    assert set(by) == {"Some Product", "{G3}"}
    assert by["Some Product"].source == "Active Setup"
    assert by["Some Product"].command == "\"C:\\P\\stub.exe\" /user"
    assert by["{G3}"].location.startswith("HKLM\\SOFTWARE\\WOW6432Node")


def test_print_monitor_bare_dll_resolves_to_system32(monkeypatch):
    mon = "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Print\\Monitors"
    _fake_registry(monkeypatch, {
        mon + "\\Local Port": {"Driver": "localspl.dll"},
        mon + "\\Vendor": {"Driver": "C:\\Vendor\\mon.dll"},
        mon + "\\NoDriver": {},
    })
    inv = p.Inventory()
    p.read_print_monitors(inv)
    by = {i.name: i for i in inv.items}
    assert set(by) == {"Local Port", "Vendor"}
    assert by["Local Port"].source == "Print Monitor"
    assert by["Local Port"].command == os.path.join(system_root(), "System32", "localspl.dll")
    assert by["Vendor"].command == "C:\\Vendor\\mon.dll"


def test_netsh_helper_values(monkeypatch):
    _fake_registry(monkeypatch, {"HKLM\\SOFTWARE\\Microsoft\\NetSh": {
        "ifmon": "ifmon.dll", "thirdparty": "C:\\T\\h.dll", "": "ignored-default"}})
    inv = p.Inventory()
    p.read_netsh_helpers(inv)
    by = {i.name: i for i in inv.items}
    assert set(by) == {"ifmon", "thirdparty"} and by["ifmon"].source == "Netsh Helper"
    assert by["ifmon"].command == os.path.join(system_root(), "System32", "ifmon.dll")


def _it(source, publisher, status, exe=""):
    it = p.Item("n", "c", source, "Machine", "loc")
    it.publisher, it.trust, it.exe = publisher, status, exe
    return it


_SYS32 = os.path.join(system_root(), "System32", "kernel32.dll")
_WOW = os.path.join(system_root(), "SysWOW64", "kernel32.dll")


def test_filter_drops_signed_files_in_windows_dirs_only(tmp_path):
    outside = str(tmp_path / "x.dll")
    (tmp_path / "x.dll").write_bytes(b"MZ")
    items = [
        _it("Netsh Helper", "Microsoft Windows", _trust.SIGNED_CATALOG, _SYS32),
        _it("Netsh Helper", "Microsoft Windows", _trust.SIGNED, _WOW),
        _it("Netsh Helper", "Microsoft Corporation", _trust.SIGNED, outside),
        _it("Print Monitor", "Microsoft Windows", _trust.SIGNED_CATALOG, outside),
        _it("Print Monitor", None, _trust.UNKNOWN, _SYS32),
        _it("Active Setup", "Microsoft Corporation", _trust.UNSIGNED, _SYS32),
        _it("SilentProcessExit", "Microsoft Corporation", _trust.SIGNED, _SYS32),
        _it("Run", "Microsoft Corporation", _trust.SIGNED, _SYS32),
    ]
    inv = p.Inventory(items=items)
    p.drop_windows_own(inv)
    assert [(i.source, i.exe) for i in inv.items] == [
        ("Netsh Helper", outside), ("Print Monitor", outside), ("Print Monitor", _SYS32),
        ("Active Setup", _SYS32), ("Run", _SYS32)]


def test_system32_lookalike_prefix_is_not_system32():
    near = os.path.join(system_root(), "System32evil", "x.dll")
    inv = p.Inventory(items=[_it("Netsh Helper", "Microsoft", _trust.SIGNED, near)])
    p.drop_windows_own(inv)
    assert len(inv.items) == 1


@pytest.mark.parametrize("flag,active", [(0x200, True), ("200", True), ("0x200", True), ("0X200", True),
                                         (0x100, False), ("100", False), ("0x0", False)])
def test_globalflag_dword_and_hex_string(monkeypatch, flag, active):
    _fake_registry(monkeypatch, {_SPE + "\\a.exe": {"MonitorProcess": "C:\\x\\e.exe"},
                                 _IFEO + "\\a.exe": {"GlobalFlag": flag}})
    inv = p.Inventory()
    p.read_silent_process_exit(inv)
    assert bool(inv.items[0].inactive) is (not active)


def test_refused_globalflag_is_unknown_not_inactive(monkeypatch):
    _fake_registry(monkeypatch, {_SPE + "\\a.exe": {"MonitorProcess": "C:\\x\\e.exe"},
                                 _IFEO + "\\a.exe": {"GlobalFlag": 0x200}},
                   refused=[_IFEO + "\\a.exe"])
    inv = p.Inventory()
    p.read_silent_process_exit(inv)
    item = inv.items[0]
    assert item.inactive == "activation unknown: GlobalFlag unreadable"
    assert not item.inactive.startswith("inactive")
    assert len(inv.problems) == 1 and "Image File Execution Options" in inv.problems[0]
    p.assess(item, p.datetime.now())
    assert any(n.code == "silentexit" and "activation unknown: GlobalFlag unreadable" in n.text
               for n in item.notes)


def test_enumeration_error_other_than_no_more_items_is_a_problem(monkeypatch):
    _fake_registry(monkeypatch, {"HKLM\\SOFTWARE\\Microsoft\\NetSh": {"a": "a.dll", "b": "b.dll"}})

    def boom(key, i):
        if i == 1:
            raise OSError(5, "Access is denied")
        return "a", "a.dll", 1
    monkeypatch.setattr(p.winreg, "EnumValue", boom)
    inv = p.Inventory()
    p.read_netsh_helpers(inv)
    assert len(inv.items) == 1
    assert len(inv.problems) == 1 and "NetSh" in inv.problems[0]


def test_netsh_wow6432_view_resolves_against_syswow64(monkeypatch):
    _fake_registry(monkeypatch, {
        "HKLM\\SOFTWARE\\Microsoft\\NetSh": {"h64": "h.dll"},
        "HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\NetSh": {"h32": "h.dll", "t": "C:\\T\\t.dll"}})
    inv = p.Inventory()
    p.read_netsh_helpers(inv)
    by = {i.name: i for i in inv.items}
    assert by["h64"].command == os.path.join(system_root(), "System32", "h.dll")
    assert by["h32"].command == os.path.join(system_root(), "SysWOW64", "h.dll")
    assert by["t"].command == "C:\\T\\t.dll"


def test_legacy_active_setup_stubs_dropped_by_exact_pair_only(monkeypatch):
    base = "HKLM\\SOFTWARE\\Microsoft\\Active Setup\\Installed Components"
    _fake_registry(monkeypatch, {
        base + "\\{2C7339CF-2B09-4501-B3F3-F3508C9228ED}": {"": "Themes Setup", "StubPath": "/UserInstall"},
        base + "\\{89820200-ecbd-11CF-8b85-00aa005b4340}": {"": "Windows Desktop Update", "StubPath": "U"},
        base + "\\{OTHER-GUID}": {"": "Themes Setup", "StubPath": "/UserInstall"},
        base + "\\{2C7339CF-2B09-4501-B3F3-F3508C9228ED}x": {"": "Windows Desktop Update", "StubPath": "U"},
    })
    inv = p.Inventory()
    p.read_active_setup(inv)
    assert sorted(i.location.rsplit("\\", 1)[1] for i in inv.items) == [
        "{2C7339CF-2B09-4501-B3F3-F3508C9228ED}x", "{OTHER-GUID}"]


def _stand_in_root(monkeypatch, tmp_path):
    root = tmp_path / "Win"
    (root / "System32").mkdir(parents=True)
    (root / "SysWOW64").mkdir()
    monkeypatch.setattr(p, "system_root", lambda: str(root))
    return root


def _signed(path):
    return p.Inventory(items=[_it("Netsh Helper", "Microsoft Corporation", _trust.SIGNED, str(path))])


def test_file_really_inside_stand_in_root_is_dropped(monkeypatch, tmp_path):
    root = _stand_in_root(monkeypatch, tmp_path)
    dll = root / "System32" / "x.dll"
    dll.write_bytes(b"MZ")
    inv = _signed(dll)
    p.drop_windows_own(inv)
    assert inv.items == []


def test_junction_out_of_system32_is_kept(monkeypatch, tmp_path):
    import _winapi
    root = _stand_in_root(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.dll").write_bytes(b"MZ")
    _winapi.CreateJunction(str(outside), str(root / "System32" / "jn"))
    inv = _signed(root / "System32" / "jn" / "x.dll")
    p.drop_windows_own(inv)
    assert len(inv.items) == 1


def test_traversal_out_of_root_is_kept(monkeypatch, tmp_path):
    root = _stand_in_root(monkeypatch, tmp_path)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "x.dll").write_bytes(b"MZ")
    inv = _signed(str(root / "System32") + "\\..\\..\\outside\\x.dll")
    p.drop_windows_own(inv)
    assert len(inv.items) == 1


def test_missing_file_is_kept(monkeypatch, tmp_path):
    root = _stand_in_root(monkeypatch, tmp_path)
    inv = _signed(root / "System32" / "gone.dll")
    p.drop_windows_own(inv)
    assert len(inv.items) == 1


def test_real_machine_has_no_windows_own_netsh_or_silent_exit():
    pythoncom = pytest.importorskip("pythoncom")
    pythoncom.CoInitialize()
    try:
        inv = p.collect()
    finally:
        pythoncom.CoUninitialize()
    assert [i.name for i in inv.items if i.source == "Netsh Helper"] == []
    assert [i.name for i in inv.items if i.source == "SilentProcessExit"] == []
