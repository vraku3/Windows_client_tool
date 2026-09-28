"""Whether Windows still has a PREVIOUS version of a device's driver
package cached in the driver store -- i.e. whether "Roll Back Driver"
(Device Manager, or `pnputil`) has anything to actually roll back to.
Read-only: this never performs a rollback, only answers whether one is
possible, and it never guesses -- a refused read stays a refused read.

Driver Manager's own context menu has carried a "Roll back to previous
version..." item that has never done more than open Device Manager, with
a tooltip admitting the reason: "Needs the previous driver still cached,
which this app does not track." That gap is real, but the tracking half
of it is not actually hard -- `modules.cleanup.cleanup_scanner.driver_store`
already asks `pnputil /enum-drivers` for the FULL contents of the driver
store (every OEM-numbered package it has ever kept, not just the one
currently bound to a device) to power Cleanup's Superseded Drivers panel,
and groups packages by (original INF name, class GUID) to tell "two
versions of the same package" apart from "two unrelated packages that
happen to share a filename". This module answers a different question
over the SAME data: not "what can be deleted" but "for THIS device's
currently-active package, is an older one still sitting in the store".

Confirmed on the real machine this was written for (2026-09-28): 31 of
its installed devices carry an OEM-numbered package (oem1.inf..oem59.inf)
-- AMD's platform drivers in particular install a new oem##.inf on nearly
every version bump rather than overwriting the old number in place, which
is exactly the pattern that leaves an older package still in the store
after an update.

`driver_store`'s own docs say `pnputil /enum-drivers` needs elevation and
unelevated prints its usage banner (exit 0, no `Published Name:` records).
Measured again here, live, confirmed genuinely unelevated (`net session`
-> "Access is denied", not just `is_admin()`): on THIS machine, right now,
`/enum-drivers` answered with real package records even without
elevation (exit code 255, not the banner-and-rc-0 shape) -- either
Windows loosened this specific read-only verb since that was written, or
it was always machine/build-dependent. Either way this module never
assumes either direction: it calls `enumerate_packages()` exactly once,
trusts ITS `None`-means-refused contract (which keys off the absence of
`Published Name:` records, not a return code or is_admin()), and neither
gates on elevation up front nor treats a working unelevated read with
suspicion. `checked=False` is the honest answer whenever the read
actually fails, on any machine; a guessed "no previous version" is not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from modules.cleanup.cleanup_scanner.driver_store import (
    DriverPackage, enumerate_packages,
)
from modules.driver_manager.driver_reader import DriverInfo, published_name_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RollbackAvailability:
    """`checked=False` means the driver store could not be enumerated at
    all (unelevated, or pnputil otherwise refused) -- never collapsed
    into `available=False`, the same distinction this codebase draws
    everywhere else a read can be refused rather than answered."""

    checked: bool
    available: bool
    previous_version: str = ""
    previous_date: Optional[date] = None
    reason: str = ""


def _older_sibling(current: DriverPackage, packages: List[DriverPackage]) -> Optional[DriverPackage]:
    """The most recent package, other than `current`, sharing its
    (original INF, class GUID) identity with a strictly OLDER date --
    a genuinely earlier install of the same package family still present
    in the store. Date only, not version: this is answering "is there
    something Device Manager could roll back to", not "which one should
    be deleted" (that stricter version-AND-date rule belongs to
    `driver_store.DriverPackage.supersedes`, which exists to avoid ever
    deleting the wrong one of two ambiguously-ordered packages -- ambiguity
    that is not a reason to hide a real, older, still-present package here)."""
    older = [
        p for p in packages
        if p is not current
        and p.original.lower() == current.original.lower()
        and p.class_guid == current.class_guid
        and p.date < current.date
    ]
    if not older:
        return None
    return max(older, key=lambda p: (p.date, p.version))


def check_rollback_availability(driver: DriverInfo) -> RollbackAvailability:
    """Looks up `driver`'s own currently-active package in the driver
    store's full enumeration and reports whether an older sibling package
    is still cached. Never runs anything, never deletes anything, never
    installs anything -- purely a read, same discipline as
    `driver_store.superseded_report()`."""
    published = published_name_for(driver.inf_name)
    if not published:
        return RollbackAvailability(
            checked=True, available=False,
            reason="This is a driver Windows ships inline (not an "
                   "installed OEM package), so there is no previous "
                   "version in the driver store to check.")

    packages = enumerate_packages()
    if packages is None:
        return RollbackAvailability(
            checked=False, available=False,
            reason="Could not enumerate the driver store -- pnputil "
                   "refused (this usually means administrator rights "
                   "are needed).")

    current = next((p for p in packages if p.published.lower() == published.lower()), None)
    if current is None:
        return RollbackAvailability(
            checked=True, available=False,
            reason=f"{published} was not found in the current driver "
                   f"store enumeration -- it may have just changed.")

    older = _older_sibling(current, packages)
    if older is None:
        return RollbackAvailability(
            checked=True, available=False,
            reason=f"No older version of this driver package is cached "
                   f"in the driver store -- {published} "
                   f"({current.version_text or 'unknown version'}) is the "
                   f"only one Windows has kept.")

    return RollbackAvailability(
        checked=True, available=True,
        previous_version=older.version_text, previous_date=older.date,
        reason=f"An older version is still cached in the driver store: "
               f"{older.published} ({older.version_text or 'unknown version'}, "
               f"{older.date.isoformat()}), versus the current "
               f"{published} ({current.version_text or 'unknown version'}, "
               f"{current.date.isoformat()}).")
