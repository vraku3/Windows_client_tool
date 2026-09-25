r"""Task Manager's Users tab: every process, filed under the account it runs as.

The Details tab lists 284 rows; the Processes tab rolls them up into Apps.
The Users tab asks a different question -- not "what is running" but "what
is EACH ACCOUNT costing this machine". One row per user, expandable to the
processes that user runs, with CPU, memory and disk summed across them.

**Grouping is by the account, and the account is read, not guessed.** The
user comes from the process token (`details.user`), the same signal the
Details tab shows. Nothing here assumes a process is SYSTEM because of its
name or its path -- the account is the fact.

**A user that could not be read is not filed under somebody else.**
Unelevated, half this machine's processes refuse their token (details.py
records 133 of 275). Those processes have no readable account, so they
cannot be attributed to anyone -- dumping them under the current user
would charge that person for SYSTEM's work, and filing them under a made-up
"Unknown" row that looks like a real account is nearly as wrong. They get
their own row that SAYS what it is, with a count and a reason, the way the
Details tab's status line already admits what it cannot see.

Qt-free, like the rest of the engine.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

#: The account-name fragments that mean "Windows itself", kept together so
#: the SYSTEM rows sort as a block below the accounts a person can act on.
_SYSTEM_ACCOUNT_HINTS = ("NT AUTHORITY", "AUTORITE NT")

#: The label of the row that holds processes whose token we could not read.
#: Deliberately not an account name: it is not a claim that those processes
#: share an account, it is the absence of one.
UNKNOWN = "Accounts not readable"


@dataclass
class UserGroup:
    """One account and the processes running under it.

    `is_system` lets the view sort real people's accounts above the SYSTEM
    block. The counts and sums are computed on demand from `rows`, never
    stored, so a stale total cannot sit beside fresh processes.
    """

    name: str
    rows: List = field(default_factory=list)
    is_system: bool = False
    is_unknown: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)

    def totals(self):
        from .grouping import totals

        return totals(self.rows)


def group_by_user(snapshot) -> List[UserGroup]:
    """File every process in `snapshot` under its account.

    Returns a list ordered for display: named accounts alphabetically
    (people first, then SYSTEM accounts), and the not-readable row last so
    its count does not sit above accounts a person can actually see.

    Processes with no readable user (unelevated, refused tokens) go into
    `UNKNOWN`. They are real processes doing real work; the row is honest
    about being unable to say whose.
    """
    buckets: Dict[str, UserGroup] = {}
    unknown: List = []
    for info in snapshot.by_pid.values():
        if info.pid == 0:       # idle time is not anybody's CPU
            continue
        user = getattr(info.details, "user", None)
        if not user:
            unknown.append(info)
            continue
        group = buckets.get(user)
        if group is None:
            group = UserGroup(name=user,
                              is_system=_is_system_account(user))
            buckets[user] = group
        group.rows.append(info)

    people = sorted((group for group in buckets.values()
                     if not group.is_system),
                    key=lambda group: group.name.lower())
    system = sorted((group for group in buckets.values()
                     if group.is_system),
                    key=lambda group: group.name.lower())
    groups = people + system
    if unknown:
        groups.append(UserGroup(name=UNKNOWN, rows=unknown, is_unknown=True))
    return groups


def _is_system_account(user: str) -> bool:
    upper = user.upper()
    return any(hint in upper for hint in _SYSTEM_ACCOUNT_HINTS)
