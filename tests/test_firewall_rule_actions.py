"""Toggling and deleting a firewall rule, including rules netsh cannot address.

Store-app rules are named with an MRT indirect string
(`@{Package_1.2_x64__abc?ms-resource://Pkg/Res/Name}`). netsh prints that name
in its listing but finds no match when it is passed back as `name=` -- and
neither does the resolved form. 33 of the 355 distinct rule names on this
machine are like that, which is why double-clicking one used to fail with a
bare "returned non-zero exit status 1".
"""
import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox

from modules.firewall_rules import firewall_manager_module as fw
from modules.firewall_rules.firewall_manager_module import (
    FirewallManagerModule, FirewallRule,
)

INDIRECT = (
    "@{Microsoft.WindowsCalculator_11.2606.0.0_x64__8wekyb3d8bbwe"
    "?ms-resource://Microsoft.WindowsCalculator/Resources/AppStoreName}"
)


def _rule(name, enabled="Yes", action="Allow", direction="Out", program=""):
    return FirewallRule(
        name=name, enabled=enabled, direction=direction, action=action,
        protocol="Any", local_port="Any", remote_port="Any",
        program=program, profile="Private",
    )


# ── resolving the indirect name ─────────────────────────────────────────────

def test_an_ordinary_name_passes_straight_through():
    assert fw.resolve_display_name("Block vlc") == "Block vlc"


def test_an_unresolvable_indirect_name_is_returned_unchanged():
    """Resolution failing must leave the caller no worse off than before."""
    assert fw.resolve_display_name("@{not-a-real-package}") == "@{not-a-real-package}"


def test_a_real_indirect_name_resolves_to_a_display_name(monkeypatch):
    # Pinned: on a machine without Calculator 11.2606.0.0 installed, MRT
    # cannot resolve the string and the OLD test failed. Pin the lookup so
    # the resolution logic is tested, not the local package versions.
    monkeypatch.setattr(fw, "_mrt_lookup",
                        lambda name: "Windows Calculator" if name == INDIRECT else "")
    resolved = fw.resolve_display_name(INDIRECT)
    assert not resolved.startswith("@{")
    assert resolved == "Windows Calculator"


# ── which backend gets used ─────────────────────────────────────────────────

@pytest.fixture
def spies(monkeypatch):
    calls = {"netsh": [], "ps": []}

    def netsh(args):
        calls["netsh"].append(args)
        return calls["netsh_result"]

    def ps(cmdlet, display_name, extra=""):
        calls["ps"].append((cmdlet, display_name, extra))
        return calls["ps_result"]

    calls["netsh_result"] = (True, "Ok.")
    calls["ps_result"] = (True, "")
    monkeypatch.setattr(fw, "_run_netsh", netsh)
    monkeypatch.setattr(fw, "_powershell_firewall", ps)
    # Pin MRT resolution so the fallback tests do not depend on the local
    # Calculator package version being 11.2606.0.0.
    monkeypatch.setattr(
        fw, "_mrt_lookup",
        lambda name: "Windows Calculator" if name == INDIRECT else "")
    return calls


def test_netsh_is_the_fast_path_and_powershell_is_not_touched(spies):
    ok, _msg = fw.set_rule_enabled("Block vlc", False)
    assert ok is True
    assert spies["netsh"][0][:5] == [
        "advfirewall", "firewall", "set", "rule", "name=Block vlc"
    ]
    assert "enable=no" in spies["netsh"][0]
    assert spies["ps"] == [], "PowerShell must not run when netsh succeeded"


def test_a_rule_netsh_cannot_address_falls_back_to_powershell(spies):
    spies["netsh_result"] = (False, "No rules match the specified criteria.")

    ok, _msg = fw.set_rule_enabled(INDIRECT, False)

    assert ok is True
    cmdlet, display_name, extra = spies["ps"][0]
    assert cmdlet == "Set-NetFirewallRule"
    assert display_name == "Windows Calculator", "must use the RESOLVED name"
    assert extra == "-Enabled False"


def test_enabling_passes_enabled_true(spies):
    spies["netsh_result"] = (False, "nope")
    fw.set_rule_enabled(INDIRECT, True)
    assert spies["ps"][0][2] == "-Enabled True"


def test_when_both_backends_fail_the_message_carries_both(spies):
    spies["netsh_result"] = (False, "No rules match the specified criteria.")
    spies["ps_result"] = (False, "Access is denied.")

    ok, message = fw.set_rule_enabled(INDIRECT, False)

    assert ok is False
    assert "No rules match" in message
    assert "Access is denied." in message


def test_delete_falls_back_the_same_way(spies):
    spies["netsh_result"] = (False, "No rules match the specified criteria.")
    ok, _msg = fw.delete_rule(INDIRECT)
    assert ok is True
    assert spies["ps"][0][0] == "Remove-NetFirewallRule"
    assert spies["ps"][0][1] == "Windows Calculator"


def test_delete_prefers_netsh_when_it_works(spies):
    ok, _msg = fw.delete_rule("Block vlc")
    assert ok is True
    assert spies["ps"] == []


# ── group toggling ───────────────────────────────────────────────────────────

def test_set_group_enabled_uses_the_group_selector_not_name(monkeypatch):
    captured = {}

    def fake_netsh(args):
        captured["args"] = args
        return True, "Ok."

    monkeypatch.setattr(fw, "_run_netsh", fake_netsh)
    ok, _msg = fw.set_group_enabled("Network Discovery", True)
    assert ok is True
    assert 'group="Network Discovery"' in captured["args"]
    assert "enable=yes" in captured["args"]
    assert not any(a.startswith("name=") for a in captured["args"])


def test_set_group_enabled_falls_back_to_powershell_group_cmdlet(monkeypatch):
    monkeypatch.setattr(fw, "_run_netsh", lambda args: (False, "elevation required"))

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    captured = {}

    def fake_run(args, **kwargs):
        captured["cmd"] = args[-1]
        return _Proc()

    monkeypatch.setattr(fw.subprocess, "run", fake_run)
    ok, _msg = fw.set_group_enabled("Remote Assistance", False)
    assert ok is True
    assert "Set-NetFirewallRule -Group 'Remote Assistance' -Enabled False" in captured["cmd"]


def test_set_group_enabled_escapes_a_quote_in_the_group_name(monkeypatch):
    monkeypatch.setattr(fw, "_run_netsh", lambda args: (False, "nope"))
    captured = {}

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(args, **kwargs):
        captured["cmd"] = args[-1]
        return _Proc()

    monkeypatch.setattr(fw.subprocess, "run", fake_run)
    fw.set_group_enabled("Bob's Group", True)
    assert "'Bob''s Group'" in captured["cmd"]


def test_set_group_enabled_reports_both_backends_when_both_fail(monkeypatch):
    monkeypatch.setattr(fw, "_run_netsh", lambda args: (False, "No rules match the specified criteria."))

    class _Proc:
        returncode, stdout, stderr = 1, "", "Access is denied."

    monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k: _Proc())
    ok, message = fw.set_group_enabled("Ghost Group", True)
    assert ok is False
    assert "No rules match" in message and "Access is denied." in message


# ── the Rule Groups dialog ───────────────────────────────────────────────────

@pytest.fixture
def groups_dialog(qapp, pane):
    groups = [fw.fa.GroupInfo("Network Discovery", 52, 22, 30, True),
              fw.fa.GroupInfo("WhatsApp", 4, 4, 0, False)]
    dlg = fw._GroupsDialog(pane._outer, groups, lambda name, enable: None)
    return dlg


def test_groups_dialog_lists_every_group_and_flags_mixed_state(groups_dialog):
    dlg = groups_dialog
    assert dlg._table.rowCount() == 2
    assert dlg._table.item(0, 0).text() == "Network Discovery"
    assert dlg._table.item(0, 4).text() == "Mixed"
    assert dlg._table.item(1, 4).text() == "On"


def test_groups_dialog_buttons_disabled_until_a_group_is_selected(groups_dialog):
    dlg = groups_dialog
    assert dlg._enable_btn.isEnabled() is False
    dlg._table.selectRow(0)
    assert dlg._enable_btn.isEnabled() is True
    assert dlg._disable_btn.isEnabled() is True


def test_groups_dialog_toggle_calls_back_with_the_selected_group(groups_dialog):
    dlg = groups_dialog
    calls = []
    dlg._toggle = lambda name, enable: calls.append((name, enable))
    dlg._table.selectRow(0)
    dlg._on_toggle(False)
    assert calls == [("Network Discovery", False)]
    # Buttons are disabled while the change is in flight.
    assert dlg._enable_btn.isEnabled() is False


def test_groups_dialog_report_failure_shows_a_warning(groups_dialog, monkeypatch):
    dlg = groups_dialog
    seen = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: seen.append(a)))
    dlg._table.selectRow(0)
    dlg.report("Network Discovery", False, "Access is denied.")
    assert seen and "Access is denied." in seen[0][2]
    assert "Could not update" in dlg._status_lbl.text()


def test_pane_opens_groups_dialog_from_the_live_snapshot(pane, qapp, monkeypatch):
    """The button reads groups off the registry snapshot the audit already
    took, not a second live read -- test_firewall_audit.py covers the
    group_summary computation itself; this pins that the pane wires it up."""
    rules = [_rule("a", direction="In")]
    extras = [fw.fa.RuleExtra(grouping="Network Discovery")]
    pane._snap = fw.fa.Snapshot(rules=rules, extras=extras, profiles=[])

    opened = {}

    class _FakeDialog:
        def __init__(self, parent, groups, toggle):
            opened["groups"] = groups
            opened["toggle"] = toggle

        def exec(self):
            opened["exec"] = True

    monkeypatch.setattr(fw, "_GroupsDialog", _FakeDialog)
    pane._open_groups_dialog()
    assert opened.get("exec") is True
    assert [g.name for g in opened["groups"]] == ["Network Discovery"]


def test_pane_shows_a_message_when_no_rule_carries_a_group(pane, qapp, monkeypatch):
    pane._snap = fw.fa.Snapshot(rules=[], extras=[], profiles=[])
    seen = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: seen.append(a)))
    pane._open_groups_dialog()
    assert seen, "an empty group list must be reported, not silently opened"


# ── the PowerShell command string ───────────────────────────────────────────

def test_a_quote_in_a_rule_name_is_escaped_not_interpolated(monkeypatch):
    """Rule names are Windows-supplied text; a stray quote must not end the
    literal and change the command."""
    captured = {}

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(args, **kwargs):
        captured["cmd"] = args[-1]
        return _Proc()

    monkeypatch.setattr(fw.subprocess, "run", fake_run)
    fw._powershell_firewall("Set-NetFirewallRule", "Bob's rule", "-Enabled False")

    assert "'Bob''s rule'" in captured["cmd"]
    assert "-ErrorAction Stop" in captured["cmd"]


def test_a_powershell_error_record_is_treated_as_failure(monkeypatch):
    """PowerShell can exit 0 while writing an ErrorRecord -- the exit code
    alone is not a success signal."""
    class _Proc:
        returncode, stdout, stderr = 0, "", "Set-NetFirewallRule : ErrorRecord blah"

    monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k: _Proc())
    ok, message = fw._powershell_firewall("Set-NetFirewallRule", "X")
    assert ok is False
    assert "ErrorRecord" in message


# ── the pane ────────────────────────────────────────────────────────────────

@pytest.fixture
def pane(qapp):
    module = FirewallManagerModule()
    module.on_start(None)
    module.create_widget()
    return module


@pytest.fixture
def warnings(monkeypatch):
    seen = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda p, t, m, *a, **k: seen.append((t, m))),
    )
    return seen


def _pump(qapp, tries=40):
    for _ in range(tries):
        QThreadPool.globalInstance().waitForDone(100)
        qapp.processEvents()


def _double_click_row_zero(pane, qapp):
    pane._table.itemDoubleClicked.emit(pane._table.item(0, 0))
    _pump(qapp)


def test_double_clicking_a_store_rule_now_succeeds_quietly(
    pane, qapp, monkeypatch, warnings
):
    """The exact failure reported: double-click on a Store-app rule."""
    monkeypatch.setattr(fw, "_run_netsh",
                        lambda args: (False, "No rules match the specified criteria."))
    monkeypatch.setattr(fw, "_powershell_firewall",
                        lambda c, d, e="": (True, ""))
    state = {"enabled": "Yes"}

    def fake_fetch():
        return [_rule(INDIRECT, enabled=state["enabled"])]

    def fake_ps(cmdlet, display, extra=""):
        state["enabled"] = "No" if extra == "-Enabled False" else "Yes"
        return True, ""

    monkeypatch.setattr(fw, "_powershell_firewall", fake_ps)
    monkeypatch.setattr(fw, "fetch_firewall_rules", fake_fetch)

    pane._on_rules_loaded(fake_fetch())
    _double_click_row_zero(pane, qapp)

    assert state["enabled"] == "No"
    assert warnings == [], "the fallback should have handled it silently"
    assert "is now disabled" in pane._status_lbl.text()


def test_a_toggle_that_does_not_take_is_reported(pane, qapp, monkeypatch, warnings):
    """Both backends can report success while the rule stays as it was; the
    pane re-reads and must say so rather than claiming it worked."""
    monkeypatch.setattr(fw, "set_rule_enabled", lambda n, e: (True, "Ok."))
    monkeypatch.setattr(fw, "fetch_firewall_rules",
                        lambda: [_rule("Stubborn rule", enabled="Yes")])

    pane._on_rules_loaded(fw.fetch_firewall_rules())
    _double_click_row_zero(pane, qapp)

    assert warnings and warnings[0][0] == "Could Not Change Rule"
    assert "Could not change rule" in pane._status_lbl.text()


def test_a_failed_toggle_shows_the_real_reason_not_an_exit_code(
    pane, qapp, monkeypatch, warnings
):
    monkeypatch.setattr(
        fw, "set_rule_enabled",
        lambda n, e: (False, "netsh: No rules match\nPowerShell: Access is denied."),
    )
    monkeypatch.setattr(fw, "fetch_firewall_rules",
                        lambda: [_rule("Locked rule", enabled="Yes")])

    pane._on_rules_loaded(fw.fetch_firewall_rules())
    _double_click_row_zero(pane, qapp)

    assert warnings
    _title, text = warnings[0]
    assert "Access is denied." in text
    assert "returned non-zero exit status" not in text


def test_a_delete_that_leaves_the_rule_behind_is_reported(
    pane, qapp, monkeypatch, warnings
):
    monkeypatch.setattr(fw, "delete_rule", lambda n: (True, "Ok."))
    monkeypatch.setattr(fw, "fetch_firewall_rules",
                        lambda: [_rule("Stubborn rule")])

    # _delete_rule uses QMessageBox.warning for its own Yes/No confirmation as
    # well as for the failure report, so the stub has to answer Yes.
    def fake_warning(parent, title, text, *a, **k):
        warnings.append((title, text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))

    pane._on_rules_loaded(fw.fetch_firewall_rules())
    pane._table.selectRow(0)
    pane._delete_rule()
    _pump(qapp)

    assert [t for t, _m in warnings] == ["Delete Rule", "Could Not Delete Rule"]
    assert "Could not delete rule" in pane._status_lbl.text()
