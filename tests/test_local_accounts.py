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
