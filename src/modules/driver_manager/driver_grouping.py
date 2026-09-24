"""Collapse devices that run the same driver into one row.

Windows lists a device per physical instance, so one driver package shows up
many times: 32 logical processors are 32 "AMD Processor" rows, all the same
INF at the same version. Read as a driver list that is noise, so the table
shows one row per distinct driver with a count, and the individual devices are
one double-click away.

No Qt here, the same split the rest of this module keeps, so the rules run
without a display.

What counts as "the same driver" is deliberately strict: name, class, version,
date, publisher and INF must all match, and so must the signed state and the
error code. A device Windows reports a problem with is never folded into
healthy ones, and two versions of one driver ("AMD GPIO Controller" 3.0.5.0 and
2.2.0.136) stay two rows -- that difference is exactly what someone reading a
driver list wants to see.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from modules.driver_manager.driver_reader import DriverInfo


def group_key(d: DriverInfo) -> Tuple:
    return (d.device_name, d.driver_class, d.version, d.date, d.publisher,
            d.inf_name, bool(d.signed), d.error_code)


@dataclass(frozen=True)
class DriverGroup:
    """One row's worth: the devices that share a driver."""

    members: Tuple[DriverInfo, ...]

    @property
    def representative(self) -> DriverInfo:
        """The device every row-level action is resolved against. It is the
        first one listed, and any member would do: they share a package."""
        return self.members[0]

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def flags(self) -> str:
        """Every distinct flag across the members, in first-seen order.

        Members can differ here (only some share a hardware id with another
        device), and showing just the representative's flags would hide a
        warning that applies to part of the group."""
        seen: List[str] = []
        for member in self.members:
            for flag in _split_flags(member.flags):
                if flag not in seen:
                    seen.append(flag)
        return " ".join(seen)

    @property
    def device_ids(self) -> Tuple[str, ...]:
        return tuple(m.device_id for m in self.members if m.device_id)


def _split_flags(flags: str) -> List[str]:
    """Flags are space-joined but each carries a marker glyph and a phrase
    ("🟠 Shared Hardware ID"), so split on the glyphs, not on spaces."""
    parts: List[str] = []
    for token in (flags or "").split(" "):
        if not token:
            continue
        if token and not token[0].isalnum() and token[0] not in "(":
            parts.append(token)
        elif parts:
            parts[-1] += " " + token
        else:
            parts.append(token)
    return parts


def group_drivers(drivers: Iterable[DriverInfo]) -> List[DriverGroup]:
    """Groups in the order their first device appears; members keep order."""
    buckets: Dict[Tuple, List[DriverInfo]] = {}
    for d in drivers:
        buckets.setdefault(group_key(d), []).append(d)
    return [DriverGroup(tuple(members)) for members in buckets.values()]
