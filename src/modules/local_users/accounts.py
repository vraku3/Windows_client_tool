"""Local accounts: reads, chip predicates and findings. Qt-free.

All reads are NetUserEnum-level and work unelevated. A read that fails is
reported as a failure, never as "no users" or "no members".
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

UF_ACCOUNTDISABLE = 0x0002
UF_PASSWD_NOTREQD = 0x0020
UF_LOCKOUT = 0x0010
UF_DONT_EXPIRE_PASSWD = 0x10000

RID_ADMIN = 500
RID_GUEST = 501
STALE_DAYS = 90
NO_EXPIRY = 0xFFFFFFFF

CHIPS = ["All", "Enabled", "Disabled", "Never logged on", "Password never expires",
         "Password not required", "Administrators", "Locked out", "Stale >90 days"]


@dataclass
class Account:
    name: str
    full_name: str
    comment: str
    flags: int
    rid: int
    last_logon: int          # epoch seconds, 0 = none recorded
    password_age_s: int      # seconds since last change, 0 = never set
    num_logons: int
    profile: str
    sid: str
    groups: List[str] = field(default_factory=list)
    groups_error: str = ""

    @property
    def enabled(self) -> bool:
        return not self.flags & UF_ACCOUNTDISABLE

    @property
    def is_admin(self) -> bool:
        return any(g.lower() == "administrators" for g in self.groups)

    @property
    def password_age_days(self) -> Optional[int]:
        return self.password_age_s // 86400 if self.password_age_s else None


@dataclass
class Finding:
    severity: str            # warning | info | unknown
    account: str
    message: str


@dataclass
class PasswordPolicy:
    """The local Security Policy (`net accounts` / NetUserModalsGet), not any one
    account's own state. `None` on the two "days" fields means the policy sets no
    limit at all (Windows reports that as the sentinel 0xFFFFFFFF seconds, not 0)."""
    min_length: int
    max_age_days: Optional[int]
    min_age_days: int
    history_len: int
    force_logoff_days: Optional[int]
    lockout_threshold: int
    lockout_duration_min: Optional[int]
    lockout_window_min: int

    @property
    def summary(self) -> str:
        max_age = "no max age" if self.max_age_days is None else f"max age {self.max_age_days}d"
        if self.lockout_threshold == 0:
            lockout = "no lockout"
        else:
            dur = "until unlocked" if self.lockout_duration_min is None else f"{self.lockout_duration_min}min"
            lockout = f"lockout after {self.lockout_threshold} ({dur})"
        return (f"Min length {self.min_length} · {max_age} · min age {self.min_age_days}d · "
                f"history {self.history_len} · {lockout}")


@dataclass
class Snapshot:
    accounts: List[Account]
    group_members: Dict[str, Tuple[Optional[List[str]], str]]  # group -> (members | None, reason)
    group_comments: Dict[str, str] = field(default_factory=dict)
    policy: Optional[PasswordPolicy] = None
    policy_error: str = ""


def days_since(epoch: int, now: Optional[float] = None) -> Optional[int]:
    if not epoch:
        return None
    return int(((now if now is not None else time.time()) - epoch) // 86400)


def matches_chip(a: Account, chip: str, now: Optional[float] = None) -> bool:
    if chip == "All":
        return True
    if chip == "Enabled":
        return a.enabled
    if chip == "Disabled":
        return not a.enabled
    if chip == "Never logged on":
        return a.last_logon == 0 and a.num_logons == 0
    if chip == "Password never expires":
        return bool(a.flags & UF_DONT_EXPIRE_PASSWD)
    if chip == "Password not required":
        return bool(a.flags & UF_PASSWD_NOTREQD)
    if chip == "Administrators":
        return a.is_admin
    if chip == "Locked out":
        return bool(a.flags & UF_LOCKOUT)
    if chip == "Stale >90 days":
        d = days_since(a.last_logon, now)
        return a.enabled and d is not None and d > STALE_DAYS
    raise ValueError(chip)


def chip_counts(accounts: List[Account], now: Optional[float] = None) -> Dict[str, int]:
    return {c: sum(1 for a in accounts if matches_chip(a, c, now)) for c in CHIPS}


def search_text(a: Account) -> str:
    return " ".join([a.name, a.full_name, a.comment, a.sid, a.profile, *a.groups]).lower()


def findings(snap: Snapshot, now: Optional[float] = None) -> List[Finding]:
    out: List[Finding] = []
    admins_group = snap.group_members.get("Administrators")
    for a in snap.accounts:
        if a.rid == RID_ADMIN and a.enabled:
            out.append(Finding("warning", a.name, "The built-in Administrator account is enabled."))
        if a.rid == RID_GUEST and a.enabled:
            out.append(Finding("warning", a.name, "The Guest account is enabled."))
        if a.enabled and a.flags & UF_PASSWD_NOTREQD and a.rid != RID_GUEST:
            out.append(Finding("warning", a.name,
                               "Enabled and flagged 'password not required': a blank password is permitted "
                               "(the flag is a permission, not proof the password is blank)."))
        if a.enabled and a.flags & UF_LOCKOUT:
            out.append(Finding("warning", a.name, "Account is locked out."))
        if a.enabled and a.flags & UF_DONT_EXPIRE_PASSWD and a.is_admin:
            out.append(Finding("info", a.name, "Administrator whose password never expires."))
        d = days_since(a.last_logon, now)
        if a.enabled and d is not None and d > STALE_DAYS:
            out.append(Finding("info", a.name, f"Enabled but no logon for {d} days."))
        if a.groups_error:
            out.append(Finding("unknown", a.name, f"Group membership could not be read: {a.groups_error}"))
    if snap.policy is not None:
        p = snap.policy
        if p.history_len == 0 and p.min_age_days == 0:
            out.append(Finding(
                "warning", "Local Security Policy",
                "Password history is not enforced (0 remembered) and the minimum password age is 0 "
                "days: anyone required to change their password can set it right back to the old one "
                "immediately, defeating rotation entirely."))
        elif p.history_len == 0:
            out.append(Finding(
                "info", "Local Security Policy",
                "Password history is not enforced (0 passwords remembered): an old password can be "
                "reused once the minimum age allows it."))
    elif snap.policy_error:
        out.append(Finding("unknown", "Local Security Policy",
                           f"Could not read the local password policy: {snap.policy_error}"))
    if admins_group is None or admins_group[0] is None:
        reason = admins_group[1] if admins_group else "not read"
        out.append(Finding("unknown", "Administrators", f"Could not read the Administrators group: {reason}"))
    else:
        by_name = {a.name.lower(): a for a in snap.accounts}
        extra = []
        for m in admins_group[0]:
            local = m.split("\\")[-1].lower()
            acct = by_name.get(local)
            if acct is not None and acct.rid == RID_ADMIN:
                continue
            if acct is not None and not acct.enabled:
                continue
            extra.append(m)
        if len(extra) > 1:
            out.append(Finding("info", "Administrators",
                               f"{len(extra)} enabled members besides the built-in Administrator: "
                               + ", ".join(extra)))
    return out


def sort_key_days(a: Account, now: Optional[float] = None) -> int:
    d = days_since(a.last_logon, now)
    return -1 if d is None else d


# ── reading ──────────────────────────────────────────────────────────────

def read_snapshot(net=None, lookup_sid: Optional[Callable[[str], str]] = None) -> Snapshot:
    """Read accounts, their groups and every group's members via the Net* APIs.

    `net` is win32net (or a fake). Raises when the account list itself cannot be read.
    """
    if net is None:
        import win32net as net  # type: ignore
    if lookup_sid is None:
        lookup_sid = _lookup_sid
    accounts: List[Account] = []
    resume = 0
    while True:
        data, _, resume = net.NetUserEnum(None, 3, 0, resume)
        for u in data:
            name = u.get("name", "")
            acct = Account(
                name=name, full_name=u.get("full_name", "") or "", comment=u.get("comment", "") or "",
                flags=int(u.get("flags", 0)), rid=int(u.get("user_id", 0)),
                last_logon=int(u.get("last_logon", 0) or 0),
                password_age_s=int(u.get("password_age", 0) or 0),
                num_logons=int(u.get("num_logons", 0) or 0),
                profile=u.get("profile", "") or "", sid=lookup_sid(name))
            try:
                acct.groups = sorted(net.NetUserGetLocalGroups(None, name))
            except Exception as e:  # pywintypes.error
                logger.warning("NetUserGetLocalGroups(%s) failed: %s", name, e)
                acct.groups_error = str(e)
            accounts.append(acct)
        if not resume:
            break
    members: Dict[str, Tuple[Optional[List[str]], str]] = {}
    comments: Dict[str, str] = {}
    resume = 0
    while True:
        data, _, resume = net.NetLocalGroupEnum(None, 1, resume)
        for g in data:
            gname = g.get("name", "")
            comments[gname] = g.get("comment", "") or ""
            try:
                mem, _, _ = net.NetLocalGroupGetMembers(None, gname, 2)
                members[gname] = ([m.get("domainandname", "") for m in mem], "")
            except Exception as e:
                logger.warning("NetLocalGroupGetMembers(%s) failed: %s", gname, e)
                members[gname] = (None, str(e))
        if not resume:
            break
    accounts.sort(key=lambda a: a.name.lower())
    policy, policy_error = read_policy(net)
    return Snapshot(accounts, members, comments, policy=policy, policy_error=policy_error)


def read_policy(net=None) -> Tuple[Optional[PasswordPolicy], str]:
    """The local Security Policy: `net accounts`'s own numbers, read via
    NetUserModalsGet rather than parsed from that command's text. Levels 0 and 3
    are two separate calls (Win32 splits password settings from lockout settings);
    a level 0 or 3 that is not implemented (a fake in a test) is reported through
    `error`, never collapsed into zeroes that would read as "no policy at all"."""
    if net is None:
        import win32net as net  # type: ignore
    try:
        m0 = net.NetUserModalsGet(None, 0)
        m3 = net.NetUserModalsGet(None, 3)
    except Exception as e:
        logger.warning("NetUserModalsGet failed: %s", e)
        return None, str(e)

    def _days_or_none(seconds) -> Optional[int]:
        seconds = int(seconds or 0)
        return None if seconds >= NO_EXPIRY else seconds // 86400

    def _min_or_none(seconds) -> Optional[int]:
        seconds = int(seconds or 0)
        return None if seconds >= NO_EXPIRY else seconds // 60

    policy = PasswordPolicy(
        min_length=int(m0.get("min_passwd_len", 0)),
        max_age_days=_days_or_none(m0.get("max_passwd_age")),
        min_age_days=int(m0.get("min_passwd_age", 0) or 0) // 86400,
        history_len=int(m0.get("password_hist_len", 0)),
        force_logoff_days=_days_or_none(m0.get("force_logoff")),
        lockout_threshold=int(m3.get("lockout_threshold", 0)),
        lockout_duration_min=_min_or_none(m3.get("lockout_duration")),
        lockout_window_min=int(m3.get("lockout_observation_window", 0) or 0) // 60,
    )
    return policy, ""


def _lookup_sid(name: str) -> str:
    try:
        import win32security
        sid, _, _ = win32security.LookupAccountName(None, name)
        return win32security.ConvertSidToStringSid(sid)
    except Exception as e:
        logger.debug("SID lookup for %s failed: %s", name, e)
        return ""
