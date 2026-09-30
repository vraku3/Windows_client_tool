r""""0 features - 0 enabled" was a refusal, not a machine with no features.

`dism /online /get-features` needs elevation. Unelevated on this box,
2026-08-24:

    rc = 740
    stdout = "\nError: 740\n\nElevated permissions are required to run DISM.\n
              Use an elevated command prompt to complete these tasks.\n"

`_fetch_all_features` never looked at `returncode`. It handed that text to
`_parse_features`, which keeps only lines containing "|", found none, and
returned an empty list -- which the pane rendered as the flat assertion
"0 features - 0 enabled".

Same family as the Get-Tpm and Get-BitLockerVolume fixes: a check that could
not run is Unknown, not zero.
"""
import subprocess

import pytest

from modules.windows_features import features_module


REAL_DISM_740 = (
    "\nError: 740\n\n"
    "Elevated permissions are required to run DISM.\n"
    "Use an elevated command prompt to complete these tasks.\n"
)

REAL_DISM_TABLE = (
    "Features listing for package : Microsoft-Windows-Foundation-Package\n\n"
    "Feature Name                | State\n"
    "----------------------------| -------------\n"
    "Printing-Foundation-Features| Enabled\n"
    "TelnetClient                | Disabled\n"
)


@pytest.fixture
def dism(monkeypatch):
    def _install(returncode, stdout, stderr=""):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a[0] if a else [], returncode, stdout, stderr),
        )
    return _install


def test_a_refused_dism_does_not_report_zero_features(dism):
    dism(740, REAL_DISM_740)

    with pytest.raises(PermissionError):
        features_module._fetch_all_features()


def test_the_refusal_names_elevation_rather_than_an_exit_code(dism):
    dism(740, REAL_DISM_740)

    with pytest.raises(PermissionError) as caught:
        features_module._fetch_all_features()

    assert "administrator" in str(caught.value).lower()


def test_another_dism_failure_is_reported_but_not_as_a_denial(dism):
    dism(87, "Error: 87\nThe get-features option is unknown.\n")

    with pytest.raises(RuntimeError) as caught:
        features_module._fetch_all_features()

    assert not isinstance(caught.value, PermissionError)
    assert "87" in str(caught.value)


def test_a_successful_listing_still_parses(dism):
    dism(0, REAL_DISM_TABLE)

    features = features_module._fetch_all_features()

    assert ("Printing-Foundation-Features", "Enabled") in features
    assert ("TelnetClient", "Disabled") in features


def test_a_genuinely_empty_but_successful_listing_is_still_empty(dism):
    dism(0, "Feature Name                | State\n")

    assert features_module._fetch_all_features() == []


# ---------------------------------------------------------------------------
# The reboot banner names WHY, reusing overview_health.pending_reboot_reasons
# instead of the bare on/off core.windows_utils.is_reboot_pending() -- a
# feature toggle here sets the CBS RebootPending key specifically, and a
# returning admin wants to know it was THIS action asking for the restart,
# not an unrelated Windows Update. `None` (a refused registry read) is its
# own state, never silently folded into "no reboot needed".
# ---------------------------------------------------------------------------

def _reboot_banner(widget):
    from PyQt6.QtWidgets import QLabel
    return next(lbl for lbl in widget.findChildren(QLabel) if lbl.text().startswith("⚠"))


def test_no_reasons_hides_the_banner(qapp, monkeypatch):
    monkeypatch.setattr(features_module, "pending_reboot_reasons", lambda: [])
    mod = features_module.WindowsFeaturesModule()
    w = mod.create_widget()
    assert _reboot_banner(w).isHidden()


def test_reasons_are_named_in_the_banner_text(qapp, monkeypatch):
    monkeypatch.setattr(features_module, "pending_reboot_reasons",
                        lambda: ["servicing (CBS)", "Windows Update"])
    mod = features_module.WindowsFeaturesModule()
    w = mod.create_widget()
    banner = _reboot_banner(w)
    assert not banner.isHidden()
    assert "servicing (CBS)" in banner.text() and "Windows Update" in banner.text()


def test_a_refused_read_is_its_own_state_not_a_hidden_banner(qapp, monkeypatch):
    monkeypatch.setattr(features_module, "pending_reboot_reasons", lambda: None)
    mod = features_module.WindowsFeaturesModule()
    w = mod.create_widget()
    banner = _reboot_banner(w)
    assert not banner.isHidden()
    assert "could not be checked" in banner.text().lower()


def test_a_raised_exception_still_degrades_to_a_hidden_banner(qapp, monkeypatch):
    def _boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(features_module, "pending_reboot_reasons", _boom)
    mod = features_module.WindowsFeaturesModule()
    w = mod.create_widget()
    assert _reboot_banner(w).isHidden()


# --- _get_feature_info: the second call site that had the same bug ---
#
# `_fetch_all_features` was fixed for this refusal; `_get_feature_info`
# (the per-feature detail panel) was not. Confirmed live, unelevated,
# 2026-09-30:
#
#   dism /online /get-featureinfo /featurename:TelnetClient
#   -> rc=740, same "Elevated permissions are required to run DISM." text.
#
# Unguarded, that text was returned as the feature's own description.

REAL_DISM_FEATUREINFO_740 = REAL_DISM_740

REAL_DISM_FEATUREINFO_OK = (
    "\nFeature Name : TelnetClient\n"
    "Display Name : Telnet Client\n"
    "Description : Client for the Telnet protocol.\n"
    "Restart Required : Possible\n"
    "State : Disabled\n"
)


def test_get_feature_info_does_not_return_a_refusal_as_a_description(dism):
    dism(740, REAL_DISM_FEATUREINFO_740)

    with pytest.raises(PermissionError):
        features_module._get_feature_info("TelnetClient")


def test_get_feature_info_still_reports_a_non_elevation_failure(dism):
    dism(87, "Error: 87\nThe get-featureinfo option is unknown.\n")

    with pytest.raises(RuntimeError) as caught:
        features_module._get_feature_info("TelnetClient")

    assert not isinstance(caught.value, PermissionError)


def test_get_feature_info_still_returns_the_real_answer_on_success(dism):
    dism(0, REAL_DISM_FEATUREINFO_OK)

    info = features_module._get_feature_info("TelnetClient")

    assert "Restart Required : Possible" in info


# --- enable/disable: a failed DISM call must not be reported "complete" ---
#
# `_enable_feature`/`_disable_feature` stream DISM's own output and return
# its exit code; the module's `_run_feature_action` closure (inside
# `create_widget`) used to ignore that code entirely and always report
# "Enable complete."/"Disable complete." -- including when DISM refused
# outright. Confirmed live, unelevated, 2026-09-30:
#
#   dism /online /enable-feature /featurename:TelnetClient /norestart
#   -> rc=740 (the real numeric exit code, via $LASTEXITCODE), same wording.
#
# `_raise_if_dism_refused` is the guard `_run_feature_action` now calls on
# that returncode before reporting success; these pin the guard itself
# against the real refusal text and a genuine success.

REAL_DISM_ENABLE_740 = (
    "\nError: 740\n\n"
    "Elevated permissions are required to run DISM.\n"
    "Use an elevated command prompt to complete these tasks.\n"
)


def test_a_refused_enable_is_not_reported_as_complete():
    with pytest.raises(PermissionError):
        features_module._raise_if_dism_refused(
            740, REAL_DISM_ENABLE_740, "/enable-feature")


def test_a_dependency_or_other_enable_failure_is_reported_but_not_as_a_denial():
    with pytest.raises(RuntimeError) as caught:
        features_module._raise_if_dism_refused(
            87, "Error: 87\nThe enable-feature option is unknown.\n",
            "/enable-feature")

    assert not isinstance(caught.value, PermissionError)
    assert "87" in str(caught.value)


def test_a_genuinely_successful_enable_raises_nothing():
    features_module._raise_if_dism_refused(0, "", "/enable-feature")
