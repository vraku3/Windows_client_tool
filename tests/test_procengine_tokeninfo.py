"""The token reader behind the Process Explorer Security view.

Pins: a refusal is reported with its reason (never as an empty group
list), deny-only groups are told apart from real membership, and the
"powerful privileges ENABLED" summary counts only enabled ones.
"""
import os

from core.procengine.tokeninfo import (
    SE_GROUP_ENABLED, SE_GROUP_ENABLED_BY_DEFAULT, SE_GROUP_INTEGRITY,
    SE_GROUP_INTEGRITY_ENABLED, SE_GROUP_LOGON_ID, SE_GROUP_MANDATORY,
    SE_GROUP_OWNER, SE_GROUP_USE_FOR_DENY_ONLY, SE_PRIVILEGE_ENABLED,
    SE_PRIVILEGE_ENABLED_BY_DEFAULT, TokenGroup, TokenPrivilege,
    TokenReport, describe_group_flags, read_token, summarize,
)

MY_PID = os.getpid()


# ---- synthetic --------------------------------------------------------

def test_deny_only_administrators_reads_as_deny_not_membership():
    # The real attribute word a UAC-filtered token gives
    # BUILTIN\Administrators on this machine: 0x10.
    group = TokenGroup("BUILTIN\\Administrators", "S-1-5-32-544", 0x10)
    assert group.deny_only
    assert group.flags() == "Deny"


def test_integrity_label_is_named_integrity():
    # 0x60 -- what the Medium label carries here.
    assert describe_group_flags(
        SE_GROUP_INTEGRITY | SE_GROUP_INTEGRITY_ENABLED) == "Integrity"


def test_mandatory_owner_and_logon_flags():
    word = (SE_GROUP_MANDATORY | SE_GROUP_ENABLED_BY_DEFAULT
            | SE_GROUP_ENABLED | SE_GROUP_OWNER)
    assert describe_group_flags(word) == "Mandatory, Owner"
    logon = SE_GROUP_LOGON_ID | SE_GROUP_MANDATORY | SE_GROUP_ENABLED
    assert "Logon SID" in describe_group_flags(logon)


def test_only_enabled_powerful_privileges_are_counted():
    report = TokenReport(pid=1, privileges=[
        TokenPrivilege("SeDebugPrivilege", SE_PRIVILEGE_ENABLED),
        TokenPrivilege("SeBackupPrivilege", 0),               # present, off
        TokenPrivilege("SeChangeNotifyPrivilege",
                       SE_PRIVILEGE_ENABLED
                       | SE_PRIVILEGE_ENABLED_BY_DEFAULT),     # not powerful
    ])
    assert report.enabled_powerful() == ["SeDebugPrivilege"]
    rows = dict(summarize(report))
    assert rows["Powerful privileges enabled"] == "SeDebugPrivilege"


def test_unread_privileges_are_a_dash_not_none():
    rows = dict(summarize(TokenReport(pid=1, privileges=None)))
    assert rows["Powerful privileges enabled"] == "—"
    rows = dict(summarize(TokenReport(pid=1, privileges=[])))
    assert rows["Powerful privileges enabled"] == "none"


def test_a_refused_token_summarises_as_its_reason():
    report = TokenReport(pid=4, error="the process could not be opened — "
                                      "Access is denied (error 5)")
    assert not report.readable
    assert summarize(report) == [("Token", report.error)]


# ---- real machine -----------------------------------------------------

def test_our_own_token_reads_in_full():
    report = read_token(MY_PID)
    assert report.readable, report.error
    assert report.user and "\\" in report.user
    assert report.user_sid.startswith("S-1-5-")
    assert report.session is not None
    assert report.elevation_type in ("Default", "Full", "Limited")
    assert report.groups, "a token with no groups does not exist"
    assert report.privileges, "every token holds SeChangeNotifyPrivilege"
    names = {p.name for p in report.privileges}
    assert "SeChangeNotifyPrivilege" in names
    # The integrity label is always one of the groups.
    assert any(g.flags() == "Integrity" for g in report.groups)


def test_system_process_refuses_with_a_reason_not_an_empty_token():
    """Pid 4 refuses an unelevated caller. Elevated, it may answer --
    either way it must never come back readable-but-empty."""
    report = read_token(4)
    if report.readable:
        assert report.groups
    else:
        assert report.error and "could not be opened" in report.error
        assert report.groups is None and report.privileges is None


def test_a_dead_pid_is_a_refusal():
    report = read_token(0xFFFFFFF0)
    assert not report.readable
    assert report.error
