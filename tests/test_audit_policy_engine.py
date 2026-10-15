"""Audit subcategories addressed by GUID, read from auditpol's CSV."""
import pytest

from modules.security_dashboard import audit_policy
from modules.security_dashboard.audit_policy import SUBCATEGORIES
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.model import Category, Risk

HEADER = ("Machine Name,Policy Target,Subcategory,Subcategory GUID,"
          "Inclusion Setting,Exclusion Setting\n")
PROC = SUBCATEGORIES[0]


def _csv(setting):
    return HEADER + f"PC,System,Process Creation,{PROC.guid},{setting},\n"


@pytest.mark.parametrize("text,expected", [
    ("Success", (True, False)),
    ("Failure", (False, True)),
    ("Success and Failure", (True, True)),
    ("No Auditing", (False, False)),
])
def test_parse_r_maps_the_four_inclusion_texts(text, expected):
    assert audit_policy.parse_r(_csv(text)) == expected


def test_parse_r_unrecognised_localized_value_is_none():
    assert audit_policy.parse_r(_csv("Erfolg und Fehler")) is None
    assert audit_policy.parse_r("") is None
    assert audit_policy.parse_r(HEADER) is None


def test_read_refusal_in_stdout_with_nonzero_exit():
    out = "Error 0x00000522 occurred:\nA required privilege is not held.\n"
    setting, why = audit_policy.read(PROC, run=lambda a: (1314, out, ""))
    assert setting is None and "administrator" in why


def test_read_refusal_in_stderr_even_with_exit_zero():
    setting, why = audit_policy.read(
        PROC, run=lambda a: (0, "", "Access is denied."))
    assert setting is None and "administrator" in why


def test_read_success_and_command_uses_guid_and_csv():
    seen = []

    def run(args):
        seen.append(args)
        return 0, _csv("Success"), ""

    assert audit_policy.read(PROC, run=run) == ((True, False), None)
    assert PROC.guid in " ".join(seen[0]) and "/r" in seen[0]


def test_read_unrecognised_output_is_none_with_a_reason():
    setting, why = audit_policy.read(PROC, run=lambda a: (0, _csv("???"), ""))
    assert setting is None and why


def test_read_missing_tool_is_none_with_a_reason():
    def run(args):
        raise OSError("not found")
    setting, why = audit_policy.read(PROC, run=run)
    assert setting is None and "not found" in why


def test_set_command_is_the_guid_form():
    assert audit_policy.set_command(PROC, True, False) == (
        "auditpol /set /subcategory:{0CCE922B-69AE-11D9-BED3-505054503030} "
        "/success:enable /failure:disable")


def test_guids_are_unique_and_well_formed():
    guids = [s.guid for s in SUBCATEGORIES]
    assert len(set(guids)) == len(guids)
    for g in guids:
        assert g.startswith("{0CCE")
        assert g.endswith("-69AE-11D9-BED3-505054503030}")


def _ctl(sub):
    name = sub.name.lower().replace(" ", "_")
    return load_catalog()["audit_" + name]


@pytest.mark.parametrize("sub", SUBCATEGORIES, ids=lambda s: s.name)
def test_control_shape_and_revert_restores_the_default(sub):
    c = _ctl(sub)
    assert c.category is Category.AUDIT_LOGGING
    assert c.risk is Risk.LOW and c.requires_admin
    (off,) = c.off_steps
    s, f = audit_policy.default_flags(sub)
    assert off == {"type": "command", "cmd": audit_policy.set_command(sub, s, f)}
    (on,) = c.on_steps
    assert on["type"] == "command" and sub.guid in on["cmd"]


def test_on_steps_never_turn_off_what_windows_enables():
    lockout = next(s for s in SUBCATEGORIES if s.name == "Account Lockout")
    # wants Failure, default is Success -> both enabled
    assert _ctl(lockout).on_steps[0]["cmd"].endswith(
        "/success:enable /failure:enable")
    assert _ctl(PROC).on_steps[0]["cmd"].endswith(
        "/success:enable /failure:disable")


def test_read_value_means_everything_wanted_is_included(monkeypatch):
    lockout = next(s for s in SUBCATEGORIES if s.name == "Account Lockout")
    c = _ctl(lockout)
    for setting, expected in [((False, True), True), ((True, True), True),
                              ((True, False), False), ((False, False), False)]:
        monkeypatch.setattr(audit_policy, "read",
                            lambda sub, run=None, s=setting: (s, None))
        assert c.read() is expected


def test_refused_read_is_none_not_false(monkeypatch):
    monkeypatch.setattr(audit_policy, "read",
                        lambda sub, run=None: (None, "needs administrator rights"))
    for sub in SUBCATEGORIES:
        assert _ctl(sub).read() is None


def test_nonzero_exit_never_yields_a_value():
    good = _csv("Success")
    s, why = audit_policy.read(PROC, run=lambda a: (1314, good, ""))
    assert s is None and "administrator" in why
    s, why = audit_policy.read(PROC, run=lambda a: (5, good, ""))
    assert s is None and "exited 5" in why


@pytest.mark.parametrize("text", ["Unsuccessful", "Successo e fallimento",
                                  "No Auditing!", ""])
def test_only_the_four_exact_values_are_recognised(text):
    assert audit_policy.parse_r(_csv(text)) is None
    s, why = audit_policy.read(PROC, run=lambda a: (0, _csv(text), ""))
    assert s is None and repr(text) in why


def test_exact_values_are_case_insensitive_and_trimmed():
    assert audit_policy.parse_r(_csv("  success AND failure ")) == (True, True)


def test_parse_r_tolerates_quotes_bom_and_blank_lines():
    text = ("\ufeff\n" + HEADER + "\n"
            f'"PC","System","Process Creation","{PROC.guid}",'
            '"Success and Failure",""\n\n')
    assert audit_policy.parse_r(text) == (True, True)


def test_a_control_wanting_both_flags_needs_both(monkeypatch):
    c = _ctl(next(s for s in SUBCATEGORIES if s.name == "Logon"))
    for setting in [(True, False), (False, True)]:
        monkeypatch.setattr(audit_policy, "read",
                            lambda sub, run=None, s=setting: (s, None))
        assert c.read() is False
