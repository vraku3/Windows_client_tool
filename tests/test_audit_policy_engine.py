"""Audit subcategories read by GUID, never by localized text."""
import pytest

from modules.security_dashboard import audit_policy as ap
from modules.security_dashboard.catalog import load_catalog

PROCESS = ap.SUBCATEGORIES[0]
_H = "Machine Name,Policy Target,Subcategory,Subcategory GUID,"\
     "Inclusion Setting,Exclusion Setting"


def _csv(setting: str) -> str:
    return (f"\n{_H}\n,HOST,Process Creation,{PROCESS.guid},{setting},\n")


def fake_run(csv_text=_csv("Success and Failure"), rc=0, err=""):
    def run(args):
        return rc, csv_text, err
    return run


@pytest.mark.parametrize("text,flags", [
    ("No Auditing", (False, False)),
    ("Success", (True, False)),
    ("Failure", (False, True)),
    ("Success and Failure", (True, True)),
])
def test_the_four_inclusion_settings(text, flags):
    assert ap.parse_r(_csv(text)) == flags


def test_an_unrecognised_localized_value_is_not_guessed():
    assert ap.parse_r(_csv("Erfolg und Fehler")) is None


def test_a_privilege_refusal_is_reported_never_guessed():
    setting, reason = ap.read(
        PROCESS, run=fake_run(rc=0x522,
                              err="Error 0x00000522 occurred:\n"
                                  "A required privilege is not held by the "
                                  "client."))
    assert setting is None and reason == ap.REFUSED


def test_a_read_setting_is_parsed():
    setting, reason = ap.read(PROCESS, run=fake_run())
    assert setting == (True, True) and reason is None


def test_set_command_addresses_the_subcategory_by_guid():
    command = ap.set_command(PROCESS, True, False)
    assert command == (f"auditpol /set /subcategory:{PROCESS.guid} "
                       "/success:enable /failure:disable")
    assert "Process Creation" not in command


def test_default_flags_read_the_text():
    assert ap.default_flags("No Auditing") == (False, False)
    assert ap.default_flags("Success") == (True, False)


def test_every_subcategory_has_a_control():
    catalog = load_catalog()
    for sub in ap.SUBCATEGORIES:
        cid = "audit_" + sub.name.lower().replace(" ", "_")
        assert cid in catalog, cid
        assert catalog[cid].desired is True


def test_every_control_off_step_restores_its_default():
    catalog = load_catalog()
    for sub in ap.SUBCATEGORIES:
        cid = "audit_" + sub.name.lower().replace(" ", "_")
        control = catalog[cid]
        expected = ap.set_command(sub, *ap.default_flags(sub.default))
        assert control.off_steps[0]["cmd"] == expected


def test_a_refused_control_read_is_none(monkeypatch):
    monkeypatch.setattr(ap, "read",
                        lambda sub, run=None: (None, ap.REFUSED))
    catalog = load_catalog()
    assert catalog["audit_logon"].read() is None
    assert catalog["audit_logon"].reader()["available"] is False
