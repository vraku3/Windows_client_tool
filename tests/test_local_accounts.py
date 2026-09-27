import time

from modules.local_users import accounts as acc

NOW = 1_800_000_000.0


def mk(name, flags=0x200, rid=1001, last=0, logons=0, groups=(), age=0):
    return acc.Account(name, "", "", flags, rid, last, age, logons, "", "S-1-5-x", list(groups))


def test_chips():
    a = [
        mk("admin", 0x10200 | 0x2, rid=500, groups=["Administrators"]),
        mk("stale", last=int(NOW - 200 * 86400), logons=3),
        mk("fresh", last=int(NOW - 3 * 86400), logons=3),
        mk("blank", 0x220),
        mk("locked", 0x210),
    ]
    c = acc.chip_counts(a, NOW)
    assert c["Disabled"] == 1 and c["Enabled"] == 4
    assert c["Administrators"] == 1 and c["Password not required"] == 1
    assert c["Locked out"] == 1 and c["Stale >90 days"] == 1
    assert c["Never logged on"] == 3 and c["Password never expires"] == 1


def test_disabled_account_never_stale():
    a = mk("x", 0x202, last=int(NOW - 500 * 86400), logons=1)
    assert not acc.matches_chip(a, "Stale >90 days", NOW)


def test_findings():
    accounts = [mk("Administrator", 0x200, rid=500), mk("Guest", 0x220, rid=501),
                mk("bob", 0x220), mk("ann"), mk("cy")]
    snap = acc.Snapshot(accounts, {"Administrators": (["PC\\Administrator", "PC\\ann", "PC\\cy"], "")})
    msgs = [f.message for f in acc.findings(snap, NOW)]
    assert any("built-in Administrator" in m for m in msgs)
    assert any("Guest" in m for m in msgs)
    assert any("password not required" in m for m in msgs)
    assert any("2 enabled members" in m for m in msgs)


def test_unreadable_administrators_group_is_unknown():
    snap = acc.Snapshot([], {"Administrators": (None, "Access is denied")})
    f = acc.findings(snap, NOW)
    assert [x.severity for x in f] == ["unknown"]


class FakeNet:
    def NetUserEnum(self, *a):
        return [{"name": "u1", "flags": 0x200, "user_id": 1001}, {"name": "u2", "flags": 0x200, "user_id": 1002}], 0, 0

    def NetUserGetLocalGroups(self, _s, name):
        if name == "u2":
            raise OSError("denied")
        return ["Users", "Administrators"]

    def NetLocalGroupEnum(self, *a):
        return [{"name": "Administrators", "comment": "c"}, {"name": "Secret"}], 0, 0

    def NetLocalGroupGetMembers(self, _s, g, _l):
        if g == "Secret":
            raise OSError("denied")
        return [{"domainandname": "PC\\u1"}], 0, 0


def test_read_snapshot_keeps_refusals_apart():
    s = acc.read_snapshot(FakeNet(), lookup_sid=lambda n: "S-" + n)
    u1, u2 = s.accounts
    assert u1.is_admin and u1.sid == "S-u1"
    assert u2.groups == [] and "denied" in u2.groups_error
    assert s.group_members["Secret"][0] is None
    assert s.group_members["Administrators"][0] == ["PC\\u1"]


class FakeModals:
    """NetUserModalsGet with real-shaped data: TIMEQ_FOREVER sentinels for
    "no limit", exactly what this real machine returns for max_passwd_age
    when a policy is unlimited."""

    def __init__(self, level0, level3):
        self._level0, self._level3 = level0, level3

    def NetUserModalsGet(self, _server, level):
        return self._level0 if level == 0 else self._level3


def test_policy_weak_rotation_is_a_warning():
    policy = acc.PasswordPolicy(min_length=14, max_age_days=42, min_age_days=0,
                                 history_len=0, force_logoff_days=None,
                                 lockout_threshold=10, lockout_duration_min=10,
                                 lockout_window_min=10)
    snap = acc.Snapshot([], {}, policy=policy)
    msgs = [(f.severity, f.message) for f in acc.findings(snap, NOW)]
    assert any(sev == "warning" and "revert" not in m and "immediately" in m for sev, m in msgs)


def test_policy_history_only_zero_is_info_not_warning():
    policy = acc.PasswordPolicy(min_length=14, max_age_days=42, min_age_days=3,
                                 history_len=0, force_logoff_days=None,
                                 lockout_threshold=10, lockout_duration_min=10,
                                 lockout_window_min=10)
    snap = acc.Snapshot([], {}, policy=policy)
    sevs = [f.severity for f in acc.findings(snap, NOW) if f.account == "Local Security Policy"]
    assert sevs == ["info"]


def test_policy_healthy_history_raises_no_finding():
    policy = acc.PasswordPolicy(min_length=14, max_age_days=42, min_age_days=1,
                                 history_len=24, force_logoff_days=None,
                                 lockout_threshold=10, lockout_duration_min=10,
                                 lockout_window_min=10)
    snap = acc.Snapshot([], {}, policy=policy)
    assert not [f for f in acc.findings(snap, NOW) if f.account == "Local Security Policy"]


def test_policy_read_failure_is_unknown_not_silent():
    snap = acc.Snapshot([], {}, policy=None, policy_error="Access is denied")
    fs = [f for f in acc.findings(snap, NOW) if f.account == "Local Security Policy"]
    assert len(fs) == 1 and fs[0].severity == "unknown" and "Access is denied" in fs[0].message


def test_read_policy_never_expiring_max_age_is_none_not_a_huge_number():
    net = FakeModals(
        {"min_passwd_len": 0, "max_passwd_age": acc.NO_EXPIRY, "min_passwd_age": 0,
         "force_logoff": acc.NO_EXPIRY, "password_hist_len": 0},
        {"lockout_duration": acc.NO_EXPIRY, "lockout_observation_window": 1800, "lockout_threshold": 0},
    )
    policy, err = acc.read_policy(net)
    assert err == ""
    assert policy.max_age_days is None and policy.force_logoff_days is None
    assert policy.lockout_duration_min is None
    assert policy.lockout_threshold == 0


def test_read_policy_real_numbers_from_this_machine_shape():
    # The exact dict shapes NetUserModalsGet(None, 0) / (None, 3) returned when
    # probed live on this machine 2026-09-27 (unelevated).
    net = FakeModals(
        {"min_passwd_len": 14, "max_passwd_age": 3628800, "min_passwd_age": 0,
         "force_logoff": acc.NO_EXPIRY, "password_hist_len": 0},
        {"lockout_duration": 600, "lockout_observation_window": 600, "lockout_threshold": 10},
    )
    policy, err = acc.read_policy(net)
    assert err == ""
    assert policy.min_length == 14
    assert policy.max_age_days == 42
    assert policy.min_age_days == 0
    assert policy.history_len == 0
    assert policy.lockout_threshold == 10
    assert policy.lockout_duration_min == 10
    assert policy.lockout_window_min == 10


def test_read_policy_reports_refusal_not_zeroes():
    class Refusing:
        def NetUserModalsGet(self, *a):
            raise OSError("Access is denied")
    policy, err = acc.read_policy(Refusing())
    assert policy is None and "denied" in err


def test_read_snapshot_attaches_policy_when_net_provides_it(monkeypatch):
    class NetWithModals(FakeNet):
        def NetUserModalsGet(self, _server, level):
            if level == 0:
                return {"min_passwd_len": 8, "max_passwd_age": 5184000, "min_passwd_age": 86400,
                        "force_logoff": acc.NO_EXPIRY, "password_hist_len": 5}
            return {"lockout_duration": 1800, "lockout_observation_window": 1800, "lockout_threshold": 5}

    s = acc.read_snapshot(NetWithModals(), lookup_sid=lambda n: "S-" + n)
    assert s.policy is not None and s.policy.min_length == 8 and s.policy.history_len == 5
    assert s.policy_error == ""


def test_real_machine_read_unelevated_ranges():
    s = acc.read_snapshot()
    assert s.accounts
    ridset = {a.rid for a in s.accounts}
    assert 500 in ridset
    assert all(a.sid.startswith("S-1-5-21") for a in s.accounts)
    assert "Administrators" in s.group_members
    now = time.time()
    for a in s.accounts:
        d = acc.days_since(a.last_logon, now)
        assert d is None or -1 <= d < 30000
    # read_snapshot wires in the real local password/lockout policy too.
    assert s.policy is not None and s.policy_error == ""
    assert 0 <= s.policy.min_length <= 128
    assert s.policy.lockout_threshold == 0 or 1 <= s.policy.lockout_threshold <= 999


def test_real_machine_read_policy():
    policy, err = acc.read_policy()
    assert err == ""
    assert policy is not None
    # Measured live on this machine (unelevated) 2026-09-27 via `net accounts`:
    # min length 14, max age 42 days, min age 0, no password history, lockout
    # threshold 10 / duration 10min / window 10min. Pin ranges, not the exact
    # numbers, so a real policy change on this machine doesn't break the suite.
    assert 0 <= policy.min_length <= 128
    assert policy.max_age_days is None or 0 <= policy.max_age_days <= 3650
    assert policy.min_age_days >= 0
    assert policy.history_len >= 0
    assert policy.lockout_threshold >= 0
    assert isinstance(policy.summary, str) and policy.summary
