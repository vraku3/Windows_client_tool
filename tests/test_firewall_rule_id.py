"""Rule IDs isolate row actions; all registry reads and commands are mocked."""
from contextlib import nullcontext
from types import SimpleNamespace
import winreg

import pytest

from modules.firewall_rules import firewall_audit as fa
from modules.firewall_rules import firewall_manager_module as fw


def _rule(**changes):
    fields = dict(name="Shared", enabled="Yes", direction="In", action="Allow",
                  protocol="TCP", local_port="Any", remote_port="Any",
                  program="", profile="Private")
    fields.update(changes)
    return fw.FirewallRule(**fields)


def _registry(monkeypatch, values):
    monkeypatch.setattr(winreg, "OpenKey", lambda *a: nullcontext(object()))
    monkeypatch.setattr(winreg, "QueryInfoKey", lambda k: (0, len(values), 0))
    monkeypatch.setattr(winreg, "EnumValue", lambda k, i: values[i])

    def query(key, name):
        for value_name, value, kind in values:
            if value_name == name:
                return value, kind
        raise FileNotFoundError(name)

    monkeypatch.setattr(winreg, "QueryValueEx", query)


def test_registry_value_name_is_the_rule_id(monkeypatch):
    _registry(monkeypatch, [("CoreNet-DHCP-In", "v2.30|Name=Shared|Dir=In|Active=TRUE|", winreg.REG_SZ)])
    rules, extras = fa.read_registry_rules()
    assert rules[0].name == "Shared"
    assert extras[0].rule_id == "CoreNet-DHCP-In"


def test_unique_keys_get_ids_but_duplicate_keys_never_do(monkeypatch):
    monkeypatch.setenv("WCT_RULE_TEST", r"C:\Windows")
    rules = [_rule(), _rule(name="shared", action="Block"), _rule(direction="Out"),
             _rule(name="Path", program=r"%WCT_RULE_TEST%\app.exe"),
             _rule(name="path", program=r"C:\Windows\app.exe")]
    extras = [fa.RuleExtra(rule_id=str(i)) for i in range(5)]
    index = fa.rule_ids_index(rules, extras)
    assert index[fa.rule_key(_rule())] is None
    assert index[fa.rule_key(_rule(direction="Out"))] == "2"
    assert index[fa.rule_key(rules[3])] is None
    assert index.get(("GPO only", "In", "")) is None
    assert fa.extras_index(rules, extras)[fa.rule_key(_rule())] is extras[1]


def test_missing_id_is_not_a_target():
    assert fa.rule_ids_index([_rule()], [fa.RuleExtra()])[fa.rule_key(_rule())] is None


@pytest.mark.parametrize("operation,enabled,action", [
    ("set", True, "Set-NetFirewallRule -Enabled True"),
    ("set", False, "Set-NetFirewallRule -Enabled False"),
    ("delete", None, "Remove-NetFirewallRule"),
])
def test_id_command_selects_exactly_one_rule(monkeypatch, operation, enabled, action):
    monkeypatch.setattr(fw, "export_rule_by_id", lambda rid: "mock.reg")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fw.subprocess, "run", fake_run)
    rule_id = "Bob's *{ID}? \u2019; Write-Output X; \u2019"
    if operation == "set":
        ok, message = fw.set_rule_enabled_by_id(rule_id, enabled)
    else:
        ok, message = fw.delete_rule_by_id(rule_id)
    assert ok, message
    args, kwargs = calls[0]
    command = __import__("base64").b64decode(args[-1]).decode("utf-16-le")
    assert args[:4] == [fw.powershell_exe(), "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    # Exact, case-sensitive match in the local store -- never -Name, which takes wildcards --
    # and the ID only ever travels in the environment, never in the command text
    # (PowerShell ends a single-quoted string at U+2019 too).
    assert "-PolicyStore PersistentStore" in command
    assert "$_.Name -ceq $env:WCT_FW_RULE_ID" in command
    assert "-Name $id -PolicyStore PersistentStore" in command
    assert "WildcardPattern]::Escape($env:WCT_FW_RULE_ID)" in command
    assert "Bob" not in command and "\u2019" not in command
    assert kwargs["env"]["WCT_FW_RULE_ID"] == rule_id
    assert "$r.Count -ne 1" in command
    assert command.endswith("%s -Name $id -PolicyStore PersistentStore -ErrorAction Stop" % action)
    assert not kwargs.get("shell", False)


def test_confirm_rule_id_refuses_a_changed_list(monkeypatch):
    key = fa.rule_key(_rule())
    one = [_rule()]
    monkeypatch.setattr(fa, "read_registry_rules",
                        lambda resolve=None: ([_rule()], [fa.RuleExtra(rule_id="A")]))
    assert fw.confirm_rule_id("A", key, one)[0] is True
    # The ID moved to another rule since the table was filled.
    assert fw.confirm_rule_id("B", key, one)[0] is False
    # netsh lists a second rule under the key (e.g. a Group Policy rule).
    assert fw.confirm_rule_id("A", key, one + [_rule(action="Block")])[0] is False
    # The local store now holds two rules under the key.
    monkeypatch.setattr(fa, "read_registry_rules", lambda resolve=None: (
        [_rule(), _rule(action="Block")], [fa.RuleExtra(rule_id="A"), fa.RuleExtra(rule_id="C")]))
    assert fw.confirm_rule_id("A", key, one)[0] is False


def test_confirm_rule_id_unreadable_registry_is_not_a_yes(monkeypatch):
    def refused(resolve=None):
        raise PermissionError("denied")
    monkeypatch.setattr(fa, "read_registry_rules", refused)
    ok, message = fw.confirm_rule_id("A", ("shared", "In", ""), [_rule()])
    assert not ok and "denied" in message


@pytest.mark.parametrize("code,output", [(1, "Access denied"), (0, "ErrorRecord denied")])
def test_id_command_does_not_hide_failure(monkeypatch, code, output):
    monkeypatch.setattr(fw, "export_rule_by_id", lambda rid: "mock.reg")
    monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=code, stdout="", stderr=output))
    ok, message = fw.delete_rule_by_id("id")
    assert not ok and output in message


@pytest.mark.parametrize("active,wanted,expected", [
    ("TRUE", True, True), ("FALSE", False, True),
    ("TRUE", False, False), ("FALSE", True, False),
    ("garbage", False, False), ("", False, False),
])
def test_id_toggle_verifies_its_active_state(monkeypatch, active, wanted, expected):
    _registry(monkeypatch, [
        ("target", "v2.30|Name=Shared|Active=" + active + "|", winreg.REG_SZ),
        ("other", "v2.30|Name=Shared|Active=FALSE|", winreg.REG_SZ),
    ])
    ok, message = fa.verify_rule_by_id("target", wanted)
    assert ok is expected
    if not ok:
        assert message


def test_delete_verification_ignores_same_named_rules(monkeypatch):
    _registry(monkeypatch, [("other", "v2.30|Name=Shared|Active=TRUE|", winreg.REG_SZ)])
    assert fa.verify_rule_by_id("target")[0] is True
    assert fa.verify_rule_by_id("target", False)[0] is False
    assert fa.verify_rule_by_id("other")[0] is False


def test_malformed_existing_rule_is_not_a_confirmed_deletion(monkeypatch):
    _registry(monkeypatch, [("target", "malformed", winreg.REG_SZ)])
    assert fa.verify_rule_by_id("target")[0] is False


@pytest.mark.parametrize("enabled", [None, True, False])
@pytest.mark.parametrize("failure", [PermissionError("denied"), FileNotFoundError("store missing")])
def test_unreadable_registry_cannot_confirm(monkeypatch, enabled, failure):
    def refused(*args):
        raise failure
    monkeypatch.setattr(winreg, "OpenKey", refused)
    ok, message = fa.verify_rule_by_id("target", enabled)
    assert not ok
    assert "could not confirm" in message.lower()


def test_id_command_timeout_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(fw, "export_rule_by_id", lambda rid: "mock.reg")
    def slow(*a, **k):
        raise fw.subprocess.TimeoutExpired(cmd="powershell", timeout=90)
    monkeypatch.setattr(fw.subprocess, "run", slow)
    ok, message = fw.delete_rule_by_id("id")
    assert not ok and "may or may not" in message


def test_act_on_rule_by_id_requires_command_success_and_read_back(monkeypatch):
    """Read-back alone cannot turn a refused/timed-out command into success;
    a refused pre-check is reported as not attempted and runs nothing."""
    monkeypatch.setattr(fw, "fetch_firewall_rules", lambda: [_rule()])
    monkeypatch.setattr(fw, "confirm_rule_id", lambda rid, key, listed=None: (True, ""))
    monkeypatch.setattr(fw, "delete_rule_by_id", lambda rid: (False, "timed out"))
    monkeypatch.setattr(fa, "verify_rule_by_id", lambda rid, enabled=None: (True, ""))
    monkeypatch.setattr(fa, "take_snapshot", lambda: fa.Snapshot())
    ok, message, rules, snap = fw.act_on_rule_by_id("A", ("shared", "In", ""), None)
    assert not ok and rules and snap is not None

    ran = []
    monkeypatch.setattr(fw, "confirm_rule_id", lambda rid, key, listed=None: (False, "list changed"))
    monkeypatch.setattr(fw, "delete_rule_by_id", lambda rid: ran.append(rid) or (True, ""))
    ok, message, _, _ = fw.act_on_rule_by_id("A", ("shared", "In", ""), None)
    assert not ok and message.startswith("Not attempted") and not ran


def test_act_on_rule_by_id_survives_a_failed_list_refresh(monkeypatch):
    def broken():
        raise RuntimeError("could not read the firewall rules")
    monkeypatch.setattr(fw, "fetch_firewall_rules", broken)
    monkeypatch.setattr(fw, "confirm_rule_id", lambda rid, key, listed=None: (True, ""))
    monkeypatch.setattr(fw, "set_rule_enabled_by_id", lambda rid, on: (True, ""))
    monkeypatch.setattr(fa, "verify_rule_by_id", lambda rid, enabled=None: (True, ""))
    monkeypatch.setattr(fa, "take_snapshot", lambda: fa.Snapshot())
    ok, _, rules, _ = fw.act_on_rule_by_id("A", ("shared", "In", ""), False)
    assert ok and rules is None


# -- the pane: which rows carry an ID, and that a double-click uses it ---------

@pytest.fixture
def pane(qapp, monkeypatch):
    from modules.firewall_rules.firewall_manager_module import FirewallManagerModule
    monkeypatch.setattr(fa, "take_snapshot", lambda: fa.Snapshot())
    module = FirewallManagerModule()
    module.on_start(None)
    module.create_widget()
    return module


def _load(pane, listed, registry, ids, fresh=True):
    from PyQt6.QtCore import Qt
    pane._snap = fa.Snapshot(rules=registry, extras=[fa.RuleExtra(rule_id=i) for i in ids])
    pane._on_rules_loaded(listed, fresh_snapshot=fresh)
    return [pane._table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for r in range(pane._table.rowCount())]


def test_a_unique_row_carries_its_registry_id(pane):
    assert _load(pane, [_rule()], [_rule()], ["ID1"]) == ["ID1"]


def test_tcp_udp_pair_has_distinct_ids_and_matching_row_keys(pane):
    rules = [_rule(protocol="TCP"), _rule(protocol="UDP")]
    assert set(_load(pane, rules, rules, ["TCP-ID", "UDP-ID"])) == {"TCP-ID", "UDP-ID"}
    for row in range(2):
        protocol = pane._table.item(row, 4).text()
        assert pane._row_key(row) == fa.rule_key(_rule(protocol=protocol))
    assert fa.extras_index(rules, pane._snap.extras)[fa.rule_key(rules[0])].rule_id == "TCP-ID"


@pytest.mark.parametrize("left,right", [
    ("", "Any"), ("443,80,81,82", "80-82,443"),
    (" RPC , RPC-EPMap ", "rpc-epmap,RPC"),
    ("IPHTTPS", "IPTLSIn,IPHTTPSIn"), ("IPHTTPS", "IPTLSOut,IPHTTPSOut"),
    ("Teredo", "teredo"),
])
def test_port_identity_is_shared(left, right):
    assert fa.rule_key(_rule(protocol="6", local_port=left)) == fa.rule_key(
        _rule(protocol="TCP", local_port=right))


def test_ports_distinguish_rules():
    assert fa.rule_key(_rule(local_port="80")) != fa.rule_key(_rule(local_port="443"))
    assert fa.rule_key(_rule(remote_port="80")) != fa.rule_key(_rule(remote_port="443"))


@pytest.mark.parametrize("name,number", [("TCP", "6"), ("UDP", "17"), ("ICMPv4", "1"),
                                       ("ICMPv6", "58"), ("IGMP", "2"), ("Any", "256")])
def test_protocol_numbers_match_names(name, number):
    registry, _ = fa.parse_registry_rule("v2.30|Name=Shared|Dir=In|Protocol=" + number + "|")
    assert fa.rule_key(registry) == fa.rule_key(_rule(protocol=name))


@pytest.mark.parametrize("port", ["Ply2Disc", "FutureKeyword"])
def test_unique_old_identity_survives_a_port_mismatch(pane, monkeypatch, port):
    listed, registry = _rule(local_port=port), _rule()
    assert _load(pane, [listed], [registry], ["ID1"]) == ["ID1"]
    assert pane._extra_for(listed).rule_id == "ID1"
    monkeypatch.setattr(fa, "read_registry_rules", lambda resolve=None:
                        ([registry], [fa.RuleExtra(rule_id="ID1")]))
    assert fw.confirm_rule_id("ID1", fa.rule_key(listed), [listed])[0]
    # The fallback is unsafe as soon as either source has a second old-key match.
    assert _load(pane, [listed, _rule(protocol="UDP")], [registry], ["ID1"])[0] is None
    assert not fw.confirm_rule_id("ID1", fa.rule_key(listed), [listed, _rule(protocol="UDP")])[0]
    assert _load(pane, [listed], [registry, _rule(protocol="UDP")], ["ID1", "ID2"]) == [None]


def test_unknown_port_never_bypasses_duplicate_registry_guard():
    rules = [_rule(local_port="FutureKeyword"), _rule()]
    index = fa.rule_ids_index(rules, [fa.RuleExtra(rule_id="A"), fa.RuleExtra(rule_id="B")])
    assert fa.unique_rule_id(fa.rule_key(rules[0]), index, fa.listed_key_counts([rules[0]])) is None


def test_rule_details_and_csv_use_the_rows_unique_id(pane, monkeypatch, tmp_path):
    import csv
    from PyQt6.QtWidgets import QFileDialog
    for listed, expected in [([_rule()], "ID1"),
                             ([_rule(), _rule()], "no unique ID -- changes unavailable")]:
        _load(pane, listed, [_rule()], ["ID1"])
        pane._table.selectRow(0)
        pane._on_selection_changed()
        assert expected in pane._detail_lbl.text()
        assert "local rule (registry)" in pane._detail_lbl.text()
        assert expected in pane._row_identity(0)
        path = tmp_path / "rules.csv"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
        pane._export_csv()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows[0]["Rule ID"] == expected
        assert rows[0]["Store"] == "local rule (registry)"


def test_exact_delete_backs_up_one_fresh_value(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    rule_id = 'ID"\\/?:*'
    value = 'v2.30|Name=Say "hi"|App=C:\\Apps\\test.exe|'
    _registry(monkeypatch, [(rule_id, value, winreg.REG_SZ),
                           ("other", "do not export", winreg.REG_SZ)])
    calls = []

    def remove(rid, action):
        files = list(tmp_path.rglob("*.reg"))
        assert len(files) == 1
        calls.append((rid, action, files[0]))
        return True, ""

    monkeypatch.setattr(fw, "_powershell_exact_id", remove)
    with caplog.at_level("INFO"):
        ok, message = fw.delete_rule_by_id(rule_id)
    assert ok and calls
    path = calls[0][2]
    data = path.read_bytes()
    assert data.startswith(b"\xff\xfe")
    escape = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    assert data.decode("utf-16") == (
        "Windows Registry Editor Version 5.00\r\n\r\n[HKEY_LOCAL_MACHINE\\" +
        fa._POLICY + '\\FirewallRules]\r\n"' + escape(rule_id) + '"="' + escape(value) + '"\r\n')
    assert str(path) in message and str(path) in caplog.text
    assert "elevation" in message and "policy refresh" in message and "reboot" in message


@pytest.mark.parametrize("failure", ["read", "write", "type"])
def test_backup_failure_prevents_exact_delete(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    _registry(monkeypatch, [("id", "v2.30|Name=Shared|", winreg.REG_DWORD if failure == "type" else winreg.REG_SZ)])
    if failure == "read":
        monkeypatch.setattr(winreg, "QueryValueEx", lambda *a: (_ for _ in ()).throw(PermissionError("read denied")))
    elif failure == "write":
        (tmp_path / "WindowsTweaker").write_text("blocked")
    calls = []
    monkeypatch.setattr(fw, "_powershell_exact_id", lambda *a: calls.append(a))
    ok, message = fw.delete_rule_by_id("id")
    assert not ok and "Not attempted" in message and not calls


def test_failed_backup_cannot_be_reported_as_a_verified_deletion(monkeypatch):
    monkeypatch.setattr(fw, "confirm_rule_id", lambda *a: (True, ""))
    monkeypatch.setattr(fw, "delete_rule_by_id", lambda rid: (False, "Not attempted: backup denied"))
    monkeypatch.setattr(fw, "_try_fetch_rules", lambda: [])
    monkeypatch.setattr(fa, "take_snapshot", lambda: fa.Snapshot())
    monkeypatch.setattr(fa, "verify_rule_by_id", lambda *a: pytest.fail("No delete was attempted"))
    ok, message, _, _ = fw.act_on_rule_by_id("A", fa.rule_key(_rule()), None)
    assert not ok and "backup denied" in message


@pytest.mark.parametrize("refreshed", [True, False])
def test_delete_success_shows_the_recovery_path(pane, qapp, monkeypatch, refreshed):
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QMessageBox
    _load(pane, [_rule()], [_rule()], ["ID1"])
    pane._table.selectRow(0)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(fw, "act_on_rule_by_id", lambda *a:
                        (True, "Restorable copy: mock.reg\nRe-importing requires elevation.",
                         [] if refreshed else None, fa.Snapshot()))
    pane._delete_rule()
    for _ in range(40):
        QThreadPool.globalInstance().waitForDone(100)
        qapp.processEvents()
    assert "mock.reg" in shown[-1] and "elevation" in shown[-1]


def test_a_key_netsh_lists_twice_gets_no_id(pane):
    # e.g. a Group Policy rule beside the local one: the registry knows one.
    rows = _load(pane, [_rule(), _rule(action="Block")], [_rule()], ["ID1"])
    assert rows == [None, None]


def test_a_reload_without_a_registry_read_drops_the_ids(pane):
    assert _load(pane, [_rule()], [_rule()], ["ID1"], fresh=False) == [None]


def test_a_store_app_rule_never_gets_an_id():
    extra = fa.RuleExtra(rule_id="ID1", raw_name="@{Pkg?ms-resource://Pkg/Res/Name}")
    assert fa.rule_ids_index([_rule()], [extra]) == {}


def test_the_row_key_matches_the_rule_key(pane):
    rule = _rule(program=r"C:\Program Files\App\app.exe", direction="Out")
    _load(pane, [rule], [rule], ["ID1"])
    assert pane._row_key(0) == fa.rule_key(rule)


def test_double_click_on_an_id_row_acts_on_that_id(pane, qapp, monkeypatch):
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QMessageBox
    _load(pane, [_rule(enabled="Yes")], [_rule()], ["ID1"])
    calls = []
    monkeypatch.setattr(fw, "act_on_rule_by_id",
                        lambda rid, key, enable: calls.append((rid, key, enable)) or (True, "", None, None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(("warning", a[1]))))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(("information", a[1]))))
    pane._table.itemDoubleClicked.emit(pane._table.item(0, 0))
    for _ in range(40):
        QThreadPool.globalInstance().waitForDone(100)
        qapp.processEvents()
    assert calls == [("ID1", fa.rule_key(_rule()), False)]

    # Confirmed, but the list was not re-read (rules=None): the table is out of
    # date, so the pane says so and refuses the next action until a Refresh --
    # a second double-click would otherwise ask to disable it AGAIN.
    assert shown == [("warning", "Rule List Out of Date")]
    assert "out of date" in pane._status_lbl.text()
    pane._table.itemDoubleClicked.emit(pane._table.item(0, 0))
    qapp.processEvents()
    assert calls == [("ID1", fa.rule_key(_rule()), False)]
    assert shown[-1] == ("information", "Refresh First")
    _load(pane, [_rule(enabled="No")], [_rule()], ["ID1"])
    assert pane._list_stale is False
