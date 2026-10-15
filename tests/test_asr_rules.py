"""Attack Surface Reduction rules: per-rule, by GUID, with an honest refusal."""
import pytest

from modules.security_dashboard import asr_rules, snapshots
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.model import Category


def _prefs(ids=None, actions=None):
    return {"AttackSurfaceReductionRules_Ids": ids,
            "AttackSurfaceReductionRules_Actions": actions}


# ---- the engine -------------------------------------------------------------

def test_the_action_codes_match_the_installed_module():
    """MSFT_MpPreference.cdxml: Disabled 0, Enabled 1, AuditMode 2,
    NotConfigured 5, Warn 6."""
    assert (asr_rules.ACTION_DISABLED, asr_rules.ACTION_BLOCK,
            asr_rules.ACTION_AUDIT, asr_rules.ACTION_NOT_CONFIGURED,
            asr_rules.ACTION_WARN) == (0, 1, 2, 5, 6)


def test_reads_the_rule_map():
    rule = asr_rules.RULES[0]
    actions = asr_rules.read_actions(_prefs([rule.guid], [1]))
    assert actions == {rule.guid.lower(): 1}
    assert asr_rules.state_for(actions, rule) == asr_rules.ACTION_BLOCK


def test_a_single_element_array_arrives_as_a_string():
    """PowerShell 5.1 ConvertTo-Json serializes a 1-item array as a string."""
    rule = asr_rules.RULES[0]
    actions = asr_rules.read_actions(_prefs(rule.guid, "2"))
    assert asr_rules.state_for(actions, rule) == asr_rules.ACTION_AUDIT


def test_an_absent_rule_is_not_configured_not_unknown():
    actions = asr_rules.read_actions(_prefs([], []))
    assert asr_rules.state_for(actions, asr_rules.RULES[0]) == \
        asr_rules.ACTION_NOT_CONFIGURED


def test_defenders_own_refusal_text_is_not_a_rule_name():
    actions = asr_rules.read_actions(
        _prefs(["N/A: Must be an administrator to view exclusions"],
               ["N/A: Must be an administrator to view exclusions"]))
    assert actions is None


def test_a_non_dict_preference_is_none():
    assert asr_rules.read_actions("N/A: Must be an administrator") is None


def test_counts():
    rules = asr_rules.RULES[:3]
    actions = asr_rules.read_actions(
        _prefs([r.guid for r in rules], [1, 2, 6]))
    assert asr_rules.blocking_count(actions) == 1
    assert asr_rules.configured_count(actions) == 3


def test_microsoft_has_exactly_three_standard_rules():
    assert len(asr_rules.standard_rules()) == 3


# ---- the catalog controls ---------------------------------------------------

def _controls():
    catalog = load_catalog()
    return {f"asr_{rule.slug}": catalog[f"asr_{rule.slug}"]
            for rule in asr_rules.RULES}


def test_every_rule_has_a_defender_control():
    controls = _controls()
    assert len(controls) == len(asr_rules.RULES)
    for control in controls.values():
        assert control.category is Category.DEFENDER


def test_only_the_standard_rules_have_an_opinion():
    for control in _controls().values():
        expected = True if control.id.replace("asr_", "") in {
            r.slug for r in asr_rules.standard_rules()} else None
        assert control.desired is expected, control.id


def test_every_rule_is_set_and_cleared_by_its_own_guid():
    for rule in asr_rules.RULES:
        control = _controls()[f"asr_{rule.slug}"]
        on = control.on_steps[0]
        off = control.off_steps[0]
        assert on["type"] == "script"
        assert "Add-MpPreference" in on["command"]
        assert "Enabled" in on["command"]
        assert off["type"] == "script"
        assert "Remove-MpPreference" in off["command"]
        for step in (on, off):
            assert "revert_template" in step and "${old}" in step["revert_template"]
            assert step["revert_values"] == {"True": "Enabled", "False": "Disabled"}
            assert rule.guid in step["command"]


def test_a_turned_on_rule_reverts_with_its_previous_action():
    """The control's value is a bool, so the revert puts the rule back to
    Enabled (it was blocking) or Disabled (it was not)."""
    from modules.security_dashboard.staging import ChangeSet
    rule = asr_rules.RULES[0]
    control = _controls()[f"asr_{rule.slug}"]
    cs = ChangeSet()
    cs.add(control, True, from_value=False)
    resolved = cs.changes[0].resolved_steps()[0]
    assert "Enabled" in resolved["command"]
    assert resolved["revert_command"].endswith("Disabled")


def test_a_block_rule_reads_true(monkeypatch):
    rule = asr_rules.RULES[0]
    monkeypatch.setattr(snapshots, "mp_preference",
                        lambda: _prefs([rule.guid], [1]))
    monkeypatch.setattr(snapshots, "unavailable", lambda *a: None)
    assert _controls()[f"asr_{rule.slug}"].read() is True


def test_an_audit_rule_is_not_blocking(monkeypatch):
    rule = asr_rules.RULES[0]
    monkeypatch.setattr(snapshots, "mp_preference",
                        lambda: _prefs([rule.guid], [2]))
    monkeypatch.setattr(snapshots, "unavailable", lambda *a: None)
    control = _controls()[f"asr_{rule.slug}"]
    assert control.read() is False
    assert "Audit" in control.reader()["status"]


def test_an_unconfigured_rule_reads_false_not_none(monkeypatch):
    monkeypatch.setattr(snapshots, "mp_preference", lambda: _prefs([], []))
    monkeypatch.setattr(snapshots, "unavailable", lambda *a: None)
    assert _controls()["asr_wmi_persistence"].read() is False


def test_a_refused_preference_reads_none(monkeypatch):
    monkeypatch.setattr(snapshots, "mp_preference", lambda: {})
    monkeypatch.setattr(snapshots, "unavailable",
                        lambda *a: "Defender is not running")
    control = _controls()["asr_lsass_credential_stealing"]
    assert control.read() is None
    assert control.reader()["available"] is False


def test_a_refusal_string_in_the_blob_reads_none(monkeypatch):
    monkeypatch.setattr(snapshots, "mp_preference", lambda: _prefs(
        ["N/A: Must be an administrator to view exclusions"],
        ["N/A: Must be an administrator to view exclusions"]))
    monkeypatch.setattr(snapshots, "unavailable", lambda *a: None)
    assert _controls()["asr_lsass_credential_stealing"].read() is None


@pytest.mark.real_machine
def test_the_real_machine_reads_every_rule():
    catalog = load_catalog()
    for rule in asr_rules.RULES:
        control = catalog[f"asr_{rule.slug}"]
        assert control.read() in (True, False), rule.name
