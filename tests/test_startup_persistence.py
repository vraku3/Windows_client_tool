"""The unified startup inventory: command parsing, findings, history, toggling.

Engine tests need no display. The registry writer is exercised against a fake
`winreg` -- nothing here changes the machine.
"""
import json
import os
from datetime import datetime, timedelta

import pytest

from modules.startup_manager import persistence as P
from modules.startup_manager import trust

NOW = datetime(2026, 9, 26, 12, 0, 0)


def _item(**kw):
    base = dict(name="X", command="C:\\Tools\\x.exe", source="Run", scope="User",
                location="HKCU\\Run", enabled=True, exe="C:\\Tools\\x.exe", exists=True,
                trust=trust.SIGNED, publisher="Acme")
    base.update(kw)
    return P.Item(**base)


# ---- command parsing --------------------------------------------------------

def test_resolve_quoted_and_args(tmp_path):
    exe = tmp_path / "my app.exe"
    exe.write_text("x")
    path, args = P.resolve_command(f'"{exe}" --flag 1')
    assert path == str(exe) and args == "--flag 1"


def test_resolve_unquoted_path_with_spaces_prefers_real_file(tmp_path):
    exe = tmp_path / "dir with space" / "prog.exe"
    exe.parent.mkdir()
    exe.write_text("x")
    path, args = P.resolve_command(f"{exe} /silent")
    assert path == str(exe) and args == "/silent"


def test_resolve_doubled_quotes_from_task_xml(tmp_path):
    """Scheduled tasks report ""C:\\x\\a.exe"" -- this made 30+ tasks look like they had no program."""
    exe = tmp_path / "a b" / "a.exe"
    exe.parent.mkdir()
    exe.write_text("x")
    path, _args = P.resolve_command(f'""{exe}"" /c')
    assert path == str(exe)


def test_resolve_missing_file_is_still_returned():
    path, _ = P.resolve_command(r'"C:\definitely\not\here.exe" -x')
    assert path == r"C:\definitely\not\here.exe"


def test_resolve_bare_name_uses_path():
    path, _ = P.resolve_command("cmd /c echo")
    assert path.lower().endswith("cmd.exe") and os.path.isfile(path)


def test_resolve_empty():
    assert P.resolve_command("") == ("", "")


# ---- findings ---------------------------------------------------------------

def _codes(item):
    P.assess(item, NOW)
    return {n.code for n in item.notes}


def test_missing_file_is_a_warning():
    item = _item(exists=False, trust=trust.UNKNOWN)
    assert "missing" in _codes(item) and item.flagged


def test_unsigned_and_invalid_are_warnings_with_reason():
    assert "unsigned" in _codes(_item(trust=trust.UNSIGNED))
    item = _item(trust=trust.INVALID, trust_reason="the file has been modified since it was signed")
    assert "invalid" in _codes(item)
    assert "modified" in [n for n in item.notes if n.code == "invalid"][0].text


def test_unreadable_signature_is_not_a_verdict():
    item = _item(trust=trust.UNKNOWN, trust_reason="access denied")
    codes = _codes(item)
    assert "sigunknown" in codes and "unsigned" not in codes and not item.flagged


def test_temp_and_downloads_warn_appdata_only_informs(monkeypatch):
    monkeypatch.setenv("TEMP", r"C:\Users\u\AppData\Local\Temp")
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    hot = _item(exe=r"C:\Users\u\AppData\Local\Temp\a\x.exe")
    assert "tempdir" in _codes(hot) and hot.flagged
    warm = _item(exe=r"C:\Users\u\AppData\Roaming\Vendor\x.exe")
    assert "userdir" in _codes(warm) and not warm.flagged
    assert "userdir" not in _codes(_item(exe=r"C:\Program Files\A\a.exe"))


def test_lolbin_informs_and_encoded_args_warn():
    plain = _item(exe=r"C:\Windows\System32\cmd.exe", command=r"C:\Windows\System32\cmd.exe /c start x.bat")
    assert "lolbin" in _codes(plain) and not plain.flagged
    bad = _item(exe=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                command=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -w hidden -enc AAAA")
    assert "args" in _codes(bad) and bad.flagged


def test_recent_only_when_not_baseline():
    fresh = _item(first_seen=NOW - timedelta(days=1), baseline=False)
    assert "recent" in _codes(fresh)
    assert "recent" not in _codes(_item(first_seen=NOW - timedelta(days=1), baseline=True))
    assert "recent" not in _codes(_item(first_seen=NOW - timedelta(days=30), baseline=False))


def test_microsoft_needs_a_signature():
    assert _item(publisher="Microsoft Corporation").is_microsoft
    assert not _item(publisher="Microsoft Corporation", trust=trust.UNSIGNED).is_microsoft
    assert not _item(publisher="Acme").is_microsoft


# ---- first-seen history -------------------------------------------------------

def test_first_scan_is_baseline_second_scan_flags_only_new(tmp_path):
    path = str(tmp_path / "h.json")
    inv = P.Inventory([_item(name="old")])
    P.apply_first_seen(inv, path, NOW)
    assert inv.items[0].baseline
    later = NOW + timedelta(days=2)
    inv2 = P.Inventory([_item(name="old"), _item(name="new")])
    P.apply_first_seen(inv2, path, later)
    old, new = inv2.items
    assert old.baseline and not new.baseline
    assert new.first_seen == later and old.first_seen == NOW


def test_corrupt_history_starts_a_new_baseline(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{not json")
    inv = P.Inventory([_item()])
    P.apply_first_seen(inv, str(path), NOW)
    assert inv.items[0].baseline
    assert json.loads(path.read_text())["items"]


def test_change_log_is_capped_and_newest_first(tmp_path):
    path = str(tmp_path / "h.json")
    for n in range(P.HISTORY_CAP + 5):
        P.record_change(path, {"n": n})
    changes = P.read_changes(path)
    assert len(changes) == P.HISTORY_CAP and changes[0]["n"] == P.HISTORY_CAP + 4


# ---- toggling, against a fake registry ------------------------------------------

class _FakeReg:
    def __init__(self, keep=True):
        self.values = {}
        self.keep = keep

    def apply(self, monkeypatch):
        monkeypatch.setattr(P.winreg, "CreateKeyEx", lambda *a, **k: _Key(self))
        monkeypatch.setattr(P.winreg, "SetValueEx",
                            lambda key, name, _r, _t, data: key.reg.values.__setitem__(name.lower(), data)
                            if key.reg.keep else None)
        monkeypatch.setattr(P, "read_approved",
                            lambda hive, sub: {k: bool(v[0] & 1) for k, v in self.values.items()})


class _Key:
    def __init__(self, reg):
        self.reg = reg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_state_bytes_shape():
    assert len(P._state_bytes(True)) == 12 and P._state_bytes(True)[0] == 2
    disabled = P._state_bytes(False)
    assert len(disabled) == 12 and disabled[0] & 1


def test_disable_then_enable_reads_back(monkeypatch):
    _FakeReg().apply(monkeypatch)
    item = _item(name="Steam")
    off = P.set_enabled(item, False)
    assert off.ok and off.enabled_after is False
    on = P.set_enabled(item, True)
    assert on.ok and on.enabled_after is True


def test_readback_disagreement_is_a_failure(monkeypatch):
    _FakeReg(keep=False).apply(monkeypatch)
    result = P.set_enabled(_item(name="Steam"), False)
    assert not result.ok and "Read-back" in result.message


def test_machine_and_task_entries_are_not_switchable():
    assert P.toggle_block_reason(_item(scope="Machine")) is not None
    assert P.toggle_block_reason(_item(source="Scheduled task")) is not None
    assert P.toggle_block_reason(_item(source="Service", scope="Machine")) is not None
    assert P.toggle_block_reason(_item()) is None
    refused = P.set_enabled(_item(scope="Machine"), False)
    assert not refused.ok


def test_refused_write_reports_the_reason(monkeypatch):
    def boom(*a, **k):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(P.winreg, "CreateKeyEx", boom)
    result = P.set_enabled(_item(), False)
    assert not result.ok and "denied" in result.message


# ---- approved-state parsing -------------------------------------------------------

def test_disabled_needs_a_readable_map():
    assert P._disabled("a", None) is None
    assert P._disabled("a", None, {"a": True}) is True
    assert P._disabled("a", {}, {"a": False}) is False


# ---- chips, search, export -------------------------------------------------------

def _inventory():
    items = [
        _item(name="Good", publisher="Microsoft Corporation"),
        _item(name="NoSig", trust=trust.UNSIGNED, publisher=None),
        _item(name="Gone", exists=False, trust=trust.UNKNOWN, enabled=False),
        _item(name="Slow", impact_ms=9000, impact_count=3),
    ]
    for i in items:
        P.assess(i, NOW)
    return P.Inventory(items, ["HKLM StartupApproved\\Run could not be read"])


def test_chip_counts_and_filters():
    inv = _inventory()
    counts = P.chip_counts(inv.items)
    assert counts["All"] == 4 and counts["Not signed"] == 1 and counts["Missing file"] == 1
    assert counts["Disabled"] == 1 and counts["Slowed boot"] == 1 and counts["Not Microsoft"] == 3
    assert counts["Flagged"] == 2


def test_search_matches_every_word_across_fields():
    item = _item(name="Steam", command=r'"C:\Steam\steam.exe" -silent', publisher="Valve")
    assert P.matches_search(item, "valve silent") and not P.matches_search(item, "valve chrome")
    assert P.matches_search(item, "")


def test_csv_has_header_and_findings():
    inv = _inventory()
    rows = P.to_csv(inv.items).splitlines()
    assert rows[0].startswith("Name,Source") and len(rows) == 5
    assert "does not exist" in P.to_csv(inv.items)


def test_summary_names_flagged_items_and_unread_sources():
    text = P.summary(_inventory())
    assert "Gone" in text and "NoSig" in text and "not read" in text


def test_impact_map_keys_services_by_name_and_apps_by_path():
    class S:
        def __init__(self, kind, name, path, total):
            self.kind, self.name, self.path, self.total_ms = kind, name, path, total
    m = P.impact_map([S("App", "a", r"C:\A\a.exe", 5000), S("App", "a", r"C:\A\a.exe", 9000),
                      S("Service", "WinDefend", "", 3000)])
    assert m[r"c:\a\a.exe"] == (9000, 2) and m["svc:windefend"] == (3000, 1)


def test_svchost_services_do_not_inherit_svchosts_own_boot_event():
    item = _item(source="Service", scope="Machine", location="Dhcp", exe=r"C:\Windows\System32\svchost.exe")
    P._apply_impact(item, {r"c:\windows\system32\svchost.exe": (4000, 1)})
    assert item.impact_ms is None


# ---- signature check ----------------------------------------------------------------

def test_catalog_signed_windows_binary_is_not_called_unsigned():
    """cmd.exe has no embedded signature; the embedded-only check calls it unsigned."""
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    if not os.path.isfile(system):
        pytest.skip("no cmd.exe")
    result = trust.check(system)
    assert result.is_signed and result.publisher and "Microsoft" in result.publisher


def test_missing_file_is_unknown_not_unsigned():
    result = trust.check(r"C:\definitely\not\here.exe")
    assert result.status == trust.UNKNOWN and result.reason


# ---- the real machine ----------------------------------------------------------------

# ---- IFEO Debugger hijacks and AppInit_DLLs ---------------------------------

class _KeyCtx:
    def __init__(self, name=""):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def Close(self):
        pass


def test_ifeo_debugger_value_is_flagged(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx(a[1] if len(a) > 1 else ""))
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: "sethc.exe" if index == 0 else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: ("cmd.exe", None))

    inv = P.Inventory()
    P.read_ifeo_hijacks(inv)

    assert len(inv.items) == 1
    item = inv.items[0]
    assert item.name == "sethc.exe" and item.command == "cmd.exe" and item.source == "IFEO"


def test_ifeo_subkey_without_debugger_is_not_listed(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: "notepad.exe" if index == 0 else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (_ for _ in ()).throw(FileNotFoundError()))

    inv = P.Inventory()
    P.read_ifeo_hijacks(inv)

    assert inv.items == []


def test_an_unopenable_ifeo_root_is_a_problem_not_a_silent_empty_list(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    inv = P.Inventory()
    P.read_ifeo_hijacks(inv)

    assert inv.items == [] and len(inv.problems) == 1


def test_one_unreadable_ifeo_subkey_is_skipped_not_fatal(monkeypatch):
    import winreg

    def fake_open(root, name=None, *a, **k):
        if name == "DefenderAgentScan.exe":
            raise OSError("access denied")
        return _KeyCtx(name or "")

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: ["DefenderAgentScan.exe", "sethc.exe"][index] if index < 2
                        else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: ("cmd.exe", None))

    inv = P.Inventory()
    P.read_ifeo_hijacks(inv)

    assert len(inv.items) == 1 and inv.items[0].name == "sethc.exe"
    assert len(inv.problems) == 1 and "DefenderAgentScan.exe" in inv.problems[0]


def test_ifeo_item_gets_a_warning_note():
    item = _item(source="IFEO", name="sethc.exe", command="cmd.exe", exe="", exists=None, trust=trust.UNKNOWN)
    P.assess(item, NOW)
    assert item.has("ifeo") and item.flagged


def test_split_appinit_handles_quoted_and_bare_entries():
    assert P._split_appinit('"C:\\Program Files\\x\\a.dll" b.dll,c.dll') == [
        "C:\\Program Files\\x\\a.dll", "b.dll", "c.dll"]


def test_appinit_empty_yields_no_items(monkeypatch):
    monkeypatch.setattr(P, "_read_values",
                        lambda hive, subkey: ([("AppInit_DLLs", ""), ("LoadAppInit_DLLs", "0")], None))
    inv = P.Inventory()
    P.read_appinit_dlls(inv)
    assert inv.items == []


def test_appinit_populated_and_active_is_flagged(monkeypatch):
    monkeypatch.setattr(P, "_read_values",
                        lambda hive, subkey: ([("AppInit_DLLs", "evil.dll"), ("LoadAppInit_DLLs", "1")], None))
    inv = P.Inventory()
    P.read_appinit_dlls(inv)
    assert len(inv.items) == 1
    item = inv.items[0]
    assert item.name == "evil.dll" and item.enabled is True
    P.assess(item, NOW)
    assert item.has("appinit") and item.flagged


def test_appinit_populated_but_inactive_is_not_flagged(monkeypatch):
    monkeypatch.setattr(P, "_read_values",
                        lambda hive, subkey: ([("AppInit_DLLs", "old.dll"), ("LoadAppInit_DLLs", "0")], None))
    inv = P.Inventory()
    P.read_appinit_dlls(inv)
    assert len(inv.items) == 1
    item = inv.items[0]
    assert item.enabled is False
    P.assess(item, NOW)
    assert not item.has("appinit")


def test_appinit_refused_read_is_a_problem(monkeypatch):
    monkeypatch.setattr(P, "_read_values", lambda hive, subkey: ([], "access denied"))
    inv = P.Inventory()
    P.read_appinit_dlls(inv)
    assert inv.items == [] and inv.problems == ["access denied"]


def test_real_ifeo_and_appinit_reads_do_not_raise():
    inv = P.Inventory()
    P.read_ifeo_hijacks(inv)
    P.read_appinit_dlls(inv)
    # Confirmed clean on this real machine (0 Debugger hijacks, AppInit_DLLs
    # empty) -- this just pins that neither reader raises or floods problems.
    assert len(inv.problems) <= 2


def test_known_lsa_packages_are_not_flagged(monkeypatch):
    import winreg
    mapping = {"Authentication Packages": ["msv1_0"], "Notification Packages": ["scecli"],
               "Security Packages": ['""']}
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (mapping[name], 7))

    inv = P.Inventory()
    P.read_lsa_packages(inv)

    assert inv.items == []


def test_an_unrecognised_lsa_package_is_flagged(monkeypatch):
    import winreg
    mapping = {"Authentication Packages": ["msv1_0"], "Notification Packages": ["scecli"],
               "Security Packages": ["mimilib"]}
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (mapping[name], 7))

    inv = P.Inventory()
    P.read_lsa_packages(inv)

    assert len(inv.items) == 1
    item = inv.items[0]
    assert item.name == "mimilib" and item.source == "LSA Package" and item.extra == "Security Packages"
    P.assess(item, NOW)
    assert item.has("lsapackage") and item.flagged


def test_an_unopenable_lsa_key_is_a_problem_not_a_silent_empty_list(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    inv = P.Inventory()
    P.read_lsa_packages(inv)

    assert inv.items == [] and len(inv.problems) == 1


def test_real_lsa_packages_are_clean_on_this_machine():
    inv = P.Inventory()
    P.read_lsa_packages(inv)
    assert inv.items == [] and inv.problems == []


# ---- BootExecute -------------------------------------------------------------

def test_default_autocheck_entry_is_not_flagged(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (["autocheck autochk *"], 7))

    inv = P.Inventory()
    P.read_boot_execute(inv)

    assert inv.items == []


def test_a_chkdsk_scheduled_autocheck_variant_is_not_flagged(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda key, name: (["autocheck autochk *", "autocheck autochk /k:C: *"], 7))

    inv = P.Inventory()
    P.read_boot_execute(inv)

    assert inv.items == []


def test_an_entry_outside_the_autocheck_family_is_flagged(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda key, name: (["autocheck autochk *", "evil.exe"], 7))

    inv = P.Inventory()
    P.read_boot_execute(inv)

    assert len(inv.items) == 1
    item = inv.items[0]
    assert item.name == "evil.exe" and item.command == "evil.exe" and item.source == "BootExecute"
    P.assess(item, NOW)
    assert item.has("bootexecute") and item.flagged


def test_boot_execute_not_set_is_not_a_problem(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (_ for _ in ()).throw(FileNotFoundError()))

    inv = P.Inventory()
    P.read_boot_execute(inv)

    assert inv.items == [] and inv.problems == []


def test_an_unopenable_session_manager_key_is_a_problem(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    inv = P.Inventory()
    P.read_boot_execute(inv)

    assert inv.items == [] and len(inv.problems) == 1


def test_real_boot_execute_is_clean_on_this_machine():
    inv = P.Inventory()
    P.read_boot_execute(inv)
    assert inv.items == [] and inv.problems == []


def test_real_machine_inventory_is_plausible(tmp_path):
    pythoncom = pytest.importorskip("pythoncom")
    pythoncom.CoInitialize()
    try:
        inv = P.collect(str(tmp_path / "h.json"), [])
    finally:
        pythoncom.CoUninitialize()
    assert len(inv.items) > 10
    sources = {i.source for i in inv.items}
    assert {"Service", "Scheduled task"} <= sources
    assert all(i.name for i in inv.items)
    with_exe = [i for i in inv.items if i.exe]
    assert len(with_exe) > len(inv.items) * 0.8
    # a real machine's own Windows services must not read as "not signed"
    windows = [i for i in inv.items if i.exe.lower().startswith(r"c:\windows\system32")]
    assert windows and not [i for i in windows if i.trust == trust.UNSIGNED]
    # nothing may be flagged only for lack of a resolved program
    assert not [i for i in inv.items if i.has("missing") and not i.exe]
