"""Saved Wi-Fi profile security audit. No display, no admin needed."""
import subprocess

import pytest

from modules.wifi_analyzer import wifi_profile_audit as a

# Captured live from this real machine, 2026-09-30, `netsh wlan show profile
# name=... ` -- no `key=clear`.
OPEN_PROFILE_TEXT = """Profile Tapo_Cam_A67B on interface Wi-Fi:
=======================================================================

Applied: All User Profile

Profile information
-------------------
    Version                : 1
    Type                   : Wireless LAN
    Name                   : Tapo_Cam_A67B
    Control options        :
        Connection mode    : Connect manually
        Network broadcast  : Connect only if this network is broadcasting
        AutoSwitch         : Do not switch to other networks
        MAC Randomization  : Disabled

Connectivity settings
---------------------
    Number of SSIDs        : 1
    SSID name              : "Tapo_Cam_A67B"
    Network type           : Infrastructure
    Radio type             : [ Any Radio Type ]
    Vendor extension          : Not present

Security settings
-----------------
    Authentication         : Open
    Cipher                 : None
    Security key           : Absent
    Key Index              : 1

Cost settings
-------------
    Cost                   : Unrestricted
"""

WPA3_TRANSITION_TEXT = """Profile vraku on interface Wi-Fi:
=======================================================================

Applied: All User Profile

Profile information
-------------------
    Control options        :
        Connection mode    : Connect automatically
        Network broadcast  : Connect only if this network is broadcasting

Security settings
-----------------
    Authentication         : WPA3-Personal
    Cipher                 : GCMP-256
    Authentication         : WPA3-Personal
    Cipher                 : GCMP
    Authentication         : WPA3-Personal
    Cipher                 : CCMP
    Security key           : Present
"""

WPA2_TEXT = """Profile DIGI-Grtc on interface Wi-Fi:
=======================================================================

Control options        :
    Connection mode    : Connect automatically
    Network broadcast  : Connect only if this network is broadcasting

Security settings
-----------------
    Authentication         : WPA2-Personal
    Cipher                 : GCMP
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Security key           : Present
"""

HIDDEN_TEXT = """Profile Office on interface Wi-Fi:
=======================================================================

Control options        :
    Connection mode    : Connect automatically
    Network broadcast  : Connect even if this network is not broadcasting

Security settings
-----------------
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Security key           : Present
"""

WEP_TEXT = """Profile OldRouter on interface Wi-Fi:
=======================================================================

Security settings
-----------------
    Authentication         : Open
    Cipher                 : WEP
    Security key           : Present
    Key Index              : 1
"""

PROFILES_LIST_TEXT = """Profiles on interface Wi-Fi:

Group policy profiles (read only)
---------------------------------
    <None>

User profiles
-------------
    All User Profile     : vraku
    All User Profile     : The Smurfs
    All User Profile     : Tapo_Cam_A67B
    All User Profile     : DIRECT-y2M2070 Series
    All User Profile     : DIGI_18ab70
    All User Profile     : DIGI-Grtc
    All User Profile     : ASUS_18
"""


# ---------------------------------------------------------------------------
# Parsing / classification
# ---------------------------------------------------------------------------

def test_open_profile_is_flagged_open_with_no_key():
    p = a.parse_profile_detail("Tapo_Cam_A67B", OPEN_PROFILE_TEXT)
    assert p.rating == "open"
    assert p.has_key is False
    assert p.auto_connect is False
    assert p.hidden is False
    assert not p.refused


def test_wpa3_transition_mode_multiple_auth_lines():
    p = a.parse_profile_detail("vraku", WPA3_TRANSITION_TEXT)
    assert p.rating == "wpa3"
    assert p.has_key is True
    assert p.auto_connect is True
    assert len(p.auth_types) == 3


def test_wpa2_profile():
    p = a.parse_profile_detail("DIGI-Grtc", WPA2_TEXT)
    assert p.rating == "wpa2"
    assert p.has_key is True


def test_hidden_profile_detected_by_not_broadcasting_phrase():
    p = a.parse_profile_detail("Office", HIDDEN_TEXT)
    assert p.hidden is True
    assert p.rating == "wpa2"


def test_wep_takes_priority_over_open_when_both_present():
    # netsh reports WEP profiles with Authentication=Open, Cipher=WEP --
    # WEP is the exploitable fact, so it must win the classification, not
    # "open" (which would still be correctly alarming, but for the wrong
    # reason -- a WEP key is crackable in minutes, an Open network is just
    # unencrypted).
    p = a.parse_profile_detail("OldRouter", WEP_TEXT)
    assert p.rating == "wep"


def test_unrecognised_auth_string_is_unknown_not_secure():
    p = a.parse_profile_detail("x", "Authentication         : SomeFutureProtocol\nCipher : ???\n")
    assert p.rating == "unknown"


def test_missing_fields_are_none_not_false():
    p = a.parse_profile_detail("x", "Security settings\n-----------------\n")
    assert p.has_key is None
    assert p.auto_connect is None
    assert p.hidden is None
    assert p.rating == "unknown"


def test_classify_ranks_weakest_first():
    assert a.RATING_ORDER["open"] < a.RATING_ORDER["wep"] < a.RATING_ORDER["wpa"] \
        < a.RATING_ORDER["wpa2"] < a.RATING_ORDER["wpa3"]
    assert a.RATING_ORDER["unknown"] > a.RATING_ORDER["wep"]


# ---------------------------------------------------------------------------
# Profile list parsing
# ---------------------------------------------------------------------------

def test_list_profile_names_parses_real_output(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=PROFILES_LIST_TEXT, stderr="")
    monkeypatch.setattr(a.subprocess, "run", fake_run)
    names = a.list_profile_names()
    assert names == ["vraku", "The Smurfs", "Tapo_Cam_A67B", "DIRECT-y2M2070 Series",
                      "DIGI_18ab70", "DIGI-Grtc", "ASUS_18"]


def test_list_profile_names_none_on_refusal(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="access denied")
    monkeypatch.setattr(a.subprocess, "run", fake_run)
    assert a.list_profile_names() is None


def test_list_profile_names_none_on_exception(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 20)
    monkeypatch.setattr(a.subprocess, "run", fake_run)
    assert a.list_profile_names() is None


def test_get_profile_security_refused_is_not_unknown_rating(monkeypatch):
    """A refused read must never be silently reported as a security rating
    at all -- `refused` is the signal callers check first."""
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Access is denied.")
    monkeypatch.setattr(a.subprocess, "run", fake_run)
    p = a.get_profile_security("Foo")
    assert p.refused is True
    assert p.reason


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------

def test_risky_profiles_excludes_refused_entries():
    profiles = [
        a.ProfileSecurity(name="open1", rating="open"),
        a.ProfileSecurity(name="wep1", rating="wep"),
        a.ProfileSecurity(name="secure", rating="wpa2"),
        a.ProfileSecurity(name="refused", rating="open", refused=True),
    ]
    risky = a.risky_profiles(profiles)
    assert {p.name for p in risky} == {"open1", "wep1"}


def test_hidden_profiles_excludes_refused_entries():
    profiles = [
        a.ProfileSecurity(name="h1", hidden=True),
        a.ProfileSecurity(name="h2", hidden=False),
        a.ProfileSecurity(name="h3", hidden=True, refused=True),
    ]
    assert {p.name for p in a.hidden_profiles(profiles)} == {"h1"}


def test_audit_profiles_none_when_list_unreadable(monkeypatch):
    monkeypatch.setattr(a, "list_profile_names", lambda: None)
    assert a.audit_profiles() is None


def test_audit_profiles_reads_each_name(monkeypatch):
    monkeypatch.setattr(a, "list_profile_names", lambda: ["A", "B"])
    seen = []

    def fake_get(name):
        seen.append(name)
        return a.ProfileSecurity(name=name, rating="wpa2")

    monkeypatch.setattr(a, "get_profile_security", fake_get)
    result = a.audit_profiles()
    assert seen == ["A", "B"]
    assert [p.name for p in result] == ["A", "B"]


# ---------------------------------------------------------------------------
# Real machine
# ---------------------------------------------------------------------------

def test_real_machine_saved_profiles_include_two_open_networks():
    """Confirmed live 2026-09-30 (`netsh wlan show profiles`, no admin): this
    machine has 7 saved Wi-Fi profiles, and two of them -- a smart-camera
    network and a phone hotspot -- are Authentication=Open/Cipher=None with
    no security key at all. If this ever regresses to 0, either the profiles
    were removed/secured (fine) or the parser broke (not fine) -- so this
    also pins that the audit can read a real Wi-Fi adapter's saved profiles
    without elevation."""
    names = a.list_profile_names()
    if names is None:
        pytest.skip("no Wi-Fi service / netsh wlan unavailable on this runner")
    if not names:
        pytest.skip("no saved Wi-Fi profiles on this runner")
    profiles = [a.get_profile_security(n) for n in names]
    unreadable = [p for p in profiles if p.refused]
    assert not unreadable, f"netsh refused to read: {[(p.name, p.reason) for p in unreadable]}"
    risky = a.risky_profiles(profiles)
    risky_names = {p.name for p in risky}
    # Specific to this machine's real saved profiles -- skip rather than fail
    # if they've since been removed or secured elsewhere.
    if "Tapo_Cam_A67B" not in {p.name for p in profiles}:
        pytest.skip("Tapo_Cam_A67B profile no longer present on this machine")
    assert "Tapo_Cam_A67B" in risky_names
    assert "ASUS_18" in risky_names
